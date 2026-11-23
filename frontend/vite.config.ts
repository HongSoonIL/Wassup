import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwind from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwind()],
  base: './',
  build: {
    rollupOptions: {
      output: {
        entryFileNames: 'assets/chat.js',
        assetFileNames: info => info.names?.some(name => name.endsWith('.css'))
          ? 'assets/chat.css'
          : 'assets/[name]-[hash][extname]',
      },
    },
  },
})
