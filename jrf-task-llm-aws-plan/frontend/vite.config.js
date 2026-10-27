import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: 'build',
  },
  define: {
    // Map process.env.REACT_APP_* to import.meta.env.VITE_*
    // Keep process.env shim for any remaining CRA-style references
    'process.env': {}
  }
});
