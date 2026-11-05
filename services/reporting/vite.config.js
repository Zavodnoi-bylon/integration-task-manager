import { defineConfig } from "vite";

export default defineConfig({
  build: { target: "esnext" },
  server: {
    host: "0.0.0.0",
    port: 4173,
    strictPort: true,
    allowedHosts: ["terminal.local"],
    watch: { ignored: ["**/state/**", "**/data/**", "**/backups/**"] },
    fs: { deny: ["**/.env", "**/.env.*", "**/*.{crt,pem,key}", "**/.git/**", "**/state/**", "**/data/**", "**/backups/**", "**/*.session*"] },
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
});
