import { readFile } from 'node:fs/promises';
import { compareBundles } from './index.mjs';

const [beforePath, afterPath, id, ...extra] = process.argv.slice(2);
if (!beforePath || !afterPath || extra.length) {
  process.stderr.write('Usage: node packages/comparison/compare-cli.mjs baseline.json current.json [comparison-uuid]\n');
  process.exitCode = 1;
} else {
  try {
    const [before, after] = await Promise.all([beforePath, afterPath].map(async path => JSON.parse(await readFile(path, 'utf8'))));
    process.stdout.write(`${JSON.stringify(compareBundles(before, after, { id }), null, 2)}\n`);
  } catch (error) {
    process.stderr.write(`Comparison rejected: ${error.message}\n`);
    process.exitCode = 1;
  }
}
