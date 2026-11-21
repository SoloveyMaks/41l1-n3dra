// Validate an AnalysisBundle against the shared contract.
//
//   node analyzers/validate_bundle.mjs analyzers/examples/bundle.json <project-root>
//
// The checker lives in docs/data-contracts/validate-bundle.mjs (package C).
// This wrapper only collects the original source bytes so line/column/byte
// coordinates and evidence hashes can be verified.  If the checker is not part
// of this checkout, pass its path in CG26_CONTRACT_VALIDATOR.
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { pathToFileURL } from 'node:url';

const [, , bundlePath, projectRoot] = process.argv;
if (!bundlePath || !projectRoot) {
  console.error('usage: node analyzers/validate_bundle.mjs <bundle.json> <project-root>');
  process.exit(2);
}

const checker = process.env.CG26_CONTRACT_VALIDATOR || '../docs/data-contracts/validate-bundle.mjs';
let validateBundle;
try {
  ({ validateBundle } = await import(checker.startsWith('file:') ? checker : pathToFileURL(join(import.meta.dirname, checker)).href));
} catch (error) {
  console.error(`contract checker not found: ${checker}`);
  console.error('set CG26_CONTRACT_VALIDATOR to docs/data-contracts/validate-bundle.mjs');
  process.exit(2);
}

const skipDirs = new Set(['.git', 'build', '.gradle', 'node_modules', '__pycache__']);

function collect(root) {
  const files = new Map();
  const walk = directory => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (entry.isDirectory()) {
        if (!skipDirs.has(entry.name)) walk(join(directory, entry.name));
        continue;
      }
      if (!entry.isFile()) continue;
      const path = join(directory, entry.name);
      files.set(relative(root, path).split(sep).join('/'), readFileSync(path));
    }
  };
  walk(root);
  return files;
}

const bundle = JSON.parse(readFileSync(bundlePath, 'utf8'));
const summary = validateBundle(bundle, collect(projectRoot));
console.log(JSON.stringify(summary));