import { readFileSync, writeFileSync } from 'node:fs';
const schemaUrl = new URL('./contracts.schema.json', import.meta.url);
const schema = JSON.parse(readFileSync(schemaUrl, 'utf8'));
function typeOf(s) {
  if (s.$ref) return s.$ref.split('/').at(-1);
  if ('const' in s) return JSON.stringify(s.const);
  if (s.enum) return s.enum.map(JSON.stringify).join(' | ');
  if (s.anyOf || s.oneOf) return (s.anyOf || s.oneOf).map(typeOf).join(' | ');
  if (s.type === 'array') return `Array<${typeOf(s.items)}>`;
  if (s.type === 'object') return `{\n${Object.entries(s.properties).map(([k,v]) => `  ${k}${s.required.includes(k) ? '' : '?'}: ${typeOf(v)};`).join('\n')}\n}`;
  return {string:'string',integer:'number',number:'number',boolean:'boolean',null:'null'}[s.type];
}
const text = '// Generated from contracts.schema.json by node generate-types.mjs.\n// Numeric bounds, formats and cross-reference invariants require validation.\n\n' +
  Object.entries(schema.$defs).map(([name,def]) => `export type ${name} = ${typeOf(def)};`).join('\n\n') + '\n';
writeFileSync(new URL('./contracts.ts', import.meta.url), text);
