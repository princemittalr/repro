// Tailwind build for the web UI. The compiled CSS is committed (app/static/css/site.css), so the
// server needs no Node build step. After changing classes in app/static, rebuild it with:
//   npx -y tailwindcss@3.4.17 -c tailwind.config.js -i app/static/css/site.src.css -o app/static/css/site.css --minify
//
// Colours are semantic tokens only (defined on :root in site.src.css). Raw palette classes such as
// text-slate-400 don't exist in this build, so every colour on the site comes from the token list.
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/static/*.html", "./app/static/js/*.js"],
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      black: "#000",
      bg: token("bg"),
      "surface-1": token("surface-1"),
      "surface-2": token("surface-2"),
      "surface-3": token("surface-3"),
      line: token("line"),
      "line-strong": token("line-strong"),
      ink: token("ink"),
      "ink-2": token("ink-2"),
      muted: token("muted"),
      fix: token("fix"),
      "fix-fg": token("fix-fg"),
      bug: token("bug"),
      "bug-fg": token("bug-fg"),
      replay: token("replay"),
      ident: token("code-ident"),
      string: token("code-string"),
      keyword: token("code-keyword"),
    },
    extend: {
      fontFamily: {
        sans: ["Geist", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        mono: ["Geist Mono", "ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
