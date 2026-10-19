import { defineConfig } from 'vite';

// 独立原型：故意不接入主仓库的零构建前端（web/ 是原生 ES module + importmap）。
// 这里允许打包器存在，代价是原型目录自带 node_modules；主库一行不受影响。
export default defineConfig({
  base: './',
  server: {
    port: 5188,
    strictPort: true,
    // 把主服务的台账代理到本机同一个 origin。
    // 服务端的写路由带同源校验（origin.netloc 必须等于 host），跨源直连会被 403；
    // 走代理后浏览器看到的是 127.0.0.1:5188，校验原样通过，也不必给主服务加 CORS。
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true, changeOrigin: false },
    },
  },
  build: { target: 'es2022', outDir: 'dist', emptyOutDir: true },
});
