import react from '@vitejs/plugin-react-swc'
import { defineConfig } from 'vite'
import { viteSingleFile } from 'vite-plugin-singlefile'

export default defineConfig({
  plugins: [react(), viteSingleFile()],
  server: {
    proxy: {
      '/api': { target: 'http://127.0.0.1:8006/', changeOrigin: true, secure: false },
    },
  },
})
