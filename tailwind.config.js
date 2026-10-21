/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    "./app/templates/**/*.html",
    "./app/static/js/**/*.js",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Noto Serif Devanagari"', '"Noto Serif"', 'serif'],
        display: ['"Noto Serif Devanagari"', '"Noto Serif"', 'serif'],
        serif: ['"Noto Serif Devanagari"', '"Noto Serif"', 'serif'],
        brand: ['"Bell MT"', '"Noto Serif"', 'serif'],
      },
      maxWidth: {
        reading: '720px',
      },
      lineHeight: {
        reading: '1.85',
      }
    },
  },
  corePlugins: { preflight: false },
  plugins: [],
}
