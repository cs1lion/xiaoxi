import { defineConfig } from 'vite';

// 独立原型：故意不接入主仓库的零构建前端（web/ 是原生 ES module + importmap）。
// 这里允许打包器存在，代价是原型目录自带 node_modules；主库一行不受影响。
export default defineConfig({
  base: './',
  server: { port: 5188, strictPort: true },
  build: { target: 'es2022', outDir: 'dist', emptyOutDir: true },
});
