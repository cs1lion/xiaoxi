import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

for (const suffix of ['', '-v2', '-v3']) {
  const source = new URL(`../config/avatar-eye-calibration${suffix}.json`, import.meta.url);
  const target = new URL(`../web/avatar/eye-calibration${suffix}.json`, import.meta.url);
  const bytes = await readFile(source);
  JSON.parse(bytes.toString('utf8'));
  await writeFile(target, bytes);
  console.log(`Calibration manifest synced: ${fileURLToPath(target)}`);
}
