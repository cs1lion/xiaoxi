// Pin and serve runtime dependencies locally; no CDN required at runtime.
import { mkdir, copyFile } from 'node:fs/promises';
const files = [
  ['three/build/three.module.js', 'three/three.module.js'],
  ['three/build/three.core.js', 'three/three.core.js'],
  ['three/examples/jsm/loaders/GLTFLoader.js', 'three/addons/loaders/GLTFLoader.js'],
  ['three/examples/jsm/utils/BufferGeometryUtils.js', 'three/addons/utils/BufferGeometryUtils.js'],
  ['three/LICENSE', 'three/LICENSE'],
  ['@pixiv/three-vrm/lib/three-vrm.module.js', 'three-vrm/three-vrm.module.js'],
  ['@pixiv/three-vrm/LICENSE', 'three-vrm/LICENSE'],
];
for (const [source, target] of files) {
  const dest = new URL(`../web/vendor/${target}`, import.meta.url);
  await mkdir(new URL('.', dest), { recursive: true });
  await copyFile(new URL(`../node_modules/${source}`, import.meta.url), dest);
}
console.log('Vendored Three.js 0.180.0 and three-vrm 3.5.5 with licenses.');
