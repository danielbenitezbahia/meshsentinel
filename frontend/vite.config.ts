import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // MapLibre GL (Mesh 3D) spawnea su worker interno como módulo ES.
  worker: { format: 'es' },
})
