// Complements JSON Schema with references, coordinates and evidence provenance.
import { createHash } from 'node:crypto';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const check = (condition, message) => { if (!condition) throw new Error(message); };
const same = (a,b) => JSON.stringify(a) === JSON.stringify(b);
const scopeKeys = ['workspaceId','runId','snapshotId'];
const signatureKeys = ['parserVersion','parserHash','rulesVersion','rulesHash','advisoryVersion','advisoryHash','configVersion','scanConfigHash','domainPolicyRevisionId','domainPolicyVersion','domainPolicyHash','hmacKeyVersion'];
export function fingerprint(finding) {
  const a = finding.anchor;
  return hash(JSON.stringify(['fp/v1',finding.category,finding.ruleId,a.normalizedPath,a.symbolKey,a.normalizedContextHash,a.role,a.secretHmac]));
}
export function validateBundle(bundle, files) {
  const identity = bundle.identity;
  const scoped = x => scopeKeys.forEach(k => check(x[k] === identity[k], `scope mismatch: ${k}`));
  const signature = x => signatureKeys.forEach(k => check(x[k] === bundle.signature[k], `signature mismatch: ${k}`));
  const policyParts = ['domainPolicyRevisionId','domainPolicyVersion','domainPolicyHash'].map(k => bundle.signature[k]);
  check(policyParts.every(x => x === null) || policyParts.every(x => x !== null), 'partial domain policy identity');
  const ids = (values,kind) => {
    const index = new Map();
    for (const x of values) { scoped(x); check(!index.has(x.id), `duplicate ${kind}: ${x.id}`); index.set(x.id,x); }
    return index;
  };
  check(bundle.scanResults.length === 3 && new Set(bundle.scanResults.map(x=>x.category)).size === 3,'require three distinct scanner outcomes');
  for (const part of [...bundle.scanResults,bundle.codeFacts]) {
    scoped(part.identity); signature(part.signature);
    for (const k of ['artifactHash','snapshotManifestHash','workflowRevisionId']) check(part.identity[k] === identity[k],`identity mismatch: ${k}`);
  }
  // B may repeat shared refs across scanner/facts envelopes; bytes must agree.
  const ev = new Map();
  for (const e of [...bundle.scanResults.flatMap(x=>x.evidence),...bundle.codeFacts.evidence]) {
    scoped(e);
    check(!ev.has(e.id) || same(ev.get(e.id),e),`conflicting evidence: ${e.id}`); ev.set(e.id,e);
    const l=e.location;
    check(l.byteEnd>=l.byteStart && (l.endLine>l.line || (l.endLine===l.line && l.endColumn>=l.column)),'invalid evidence range');
    if (files) {
      const raw=files.get(l.path); check(raw instanceof Uint8Array,`source unavailable: ${l.path}`);
      check(hash(raw)===e.sourceHash,`source hash mismatch: ${l.path}`);
      const bytes=Buffer.from(raw);
      const text=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes);
      const offset=(line,column)=>{
        let pos=0;
        for(let n=1;n<line;n++){const next=text.indexOf('\n',pos);check(next!==-1,'line beyond EOF');pos=next+1;}
        const next=text.indexOf('\n',pos); const end=next===-1?text.length:next;
        let row=text.slice(pos,end); if(next!==-1&&row.endsWith('\r'))row=row.slice(0,-1); const points=Array.from(row);
        check(column<=points.length+1,'column beyond line');
        return Buffer.byteLength(text.slice(0,pos)+points.slice(0,column-1).join(''),'utf8');
      };
      check(offset(l.line,l.column)===l.byteStart && offset(l.endLine,l.endColumn)===l.byteEnd,'line/byte coordinates disagree');
    }
  }
  const nodes=ids(bundle.codeFacts.nodes,'node'), edges=ids(bundle.codeFacts.edges,'edge');
  const findings=ids(bundle.scanResults.flatMap(x=>x.findings),'finding');
  const evidenceRefs=refs=>refs.forEach(id=>check(ev.has(id),`missing evidence: ${id}`));
  for (const n of nodes.values()) { evidenceRefs(n.evidenceRefs);check(n.kind==='actor'||n.evidenceRefs.length>0,'source node without evidence'); }
  const policies=new Map();
  for(const p of bundle.codeFacts.policyInputs){check(!policies.has(p.id),'duplicate policy');policies.set(p.id,p);}
  for (const p of policies.values()) {
    check(p.workspaceId===identity.workspaceId,'policy workspace mismatch');
    check(p.id===bundle.signature.domainPolicyRevisionId && p.version===bundle.signature.domainPolicyVersion && p.hash===bundle.signature.domainPolicyHash,'policy differs from signature');
    check((p.approvalStatus==='approved')===(p.approvedBy!==null&&p.approvedAt!==null),'policy approval basis missing');
  }
  for (const e of edges.values()) {
    check(nodes.has(e.fromId)&&nodes.has(e.toId),'edge endpoints outside bundle'); evidenceRefs(e.evidenceRefs);
    check(e.basis==='source' ? e.policyRef===null&&e.evidenceRefs.length>0 : policies.has(e.policyRef),'edge provenance missing');
  }
  for (const scan of bundle.scanResults) {
    check(scan.status!=='complete'||scan.coverage.status==='complete','complete scanner has incomplete coverage');
    for(const f of scan.findings) {
      check(f.category===scan.category,'finding category differs from scanner');
      evidenceRefs([f.primaryEvidenceId,...f.relatedEvidenceIds,...f.traceEvidenceIds]);
      check(same(f.primaryLocation,ev.get(f.primaryEvidenceId).location),'primary evidence/location differs');
      check(f.anchor.normalizedPath===f.primaryLocation.path,'anchor path differs from primary path');
      check((f.category==='SECRET')===(f.anchor.secretHmac!==null),'SECRET requires HMAC, others prohibit it');
      check(f.fingerprint===fingerprint(f),'fingerprint recipe mismatch');
      f.graphNodeIds.forEach(id=>check(nodes.has(id),'finding node outside graph'));
    }
  }
  const diagnosticIds=new Map();
  for (const part of [...bundle.scanResults,bundle.codeFacts]) {
    for(const d of part.diagnostics){check(!diagnosticIds.has(d.id)||same(diagnosticIds.get(d.id),d),'conflicting diagnostic');diagnosticIds.set(d.id,d);}
    ids(part.diagnostics,'diagnostic');
    part.diagnostics.forEach(d=>{if(d.evidenceId!==null)evidenceRefs([d.evidenceId]);});
    const c=part.coverage;
    check(c.filesVisited<=c.filesTotal,'coverage visited exceeds total');
    const keys=new Set();
    for(const s of c.scopes){const k=JSON.stringify([s.category,s.path,s.ruleId]);check(!keys.has(k),'duplicate coverage scope');keys.add(k);}
    if(c.status==='complete')check(c.scopes.every(x=>x.status==='checked'||x.status==='not_applicable')&&!c.skippedScopes.length&&!c.parserErrors.length&&!c.unresolvedSymbols.length&&!c.limitsReached.length,'complete coverage contains gaps');
    if ('category' in part) {
      check(part.coverage.scopes.every(x=>x.category===part.category),'coverage category mismatch');
      check(part.diagnostics.every(d=>d.category===part.category),'scanner diagnostic category mismatch');
    }
    c.parserErrors.forEach(id=>check(part.diagnostics.some(d=>d.id===id&&(!('category' in part)||d.category===part.category)),'unknown or wrong-category parser diagnostic'));
  }
  return {findings:findings.size,evidence:ev.size,nodes:nodes.size,edges:edges.size};
}
