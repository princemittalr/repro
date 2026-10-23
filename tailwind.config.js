// Tailwind build for the web UI. The compiled CSS is committed (app/static/css/site.css), so the
// server needs no Node build step. After changing classes in app/static, rebuild it with:
//   npx -y tailwindcss@3.4.17 -c tailwind.config.js -i app/static/css/site.src.css -o app/static/css/site.css --minify
/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/static/*.html", "./app/static/js/*.js"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Geist", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["Geist Mono", "ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
