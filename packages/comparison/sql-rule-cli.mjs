import { readFile } from 'node:fs/promises';

import { compareSqlRuleScopes } from './sql-rule.mjs';

const USAGE = 'Usage: node packages/comparison/sql-rule-cli.mjs baseline.published.json current.published.json [--path <path>] [--id <uuid>]\n';

const args = process.argv.slice(2);
const positional = [];
let path;
let id;
for (let index = 0; index < args.length; index += 1) {
  const arg = args[index];
  if (arg === '--help' || arg === '-h') {
    process.stdout.write(USAGE);
    process.exit(0);
  }
  if (arg === '--path') {
    path = args[++index];
    continue;
  }
  if (arg === '--id') {
    id = args[++index];
    continue;
  }
  if (arg.startsWith('--')) {
    process.stderr.write(`SQL rule comparison rejected: unknown option ${arg}\n${USAGE}`);
    process.exit(1);
  }
  positional.push(arg);
}

if (positional.length !== 2) {
  process.stderr.write(USAGE);
  process.exit(1);
}

try {
  const [beforePath, afterPath] = positional;
  const [before, after] = await Promise.all(
    [beforePath, afterPath].map(async file => JSON.parse(await readFile(file, 'utf8'))),
  );
  const result = compareSqlRuleScopes(before, after, { path, id });
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
} catch (error) {
  process.stderr.write(`SQL rule comparison rejected: ${error?.message || error}\n`);
  process.exit(1);
}