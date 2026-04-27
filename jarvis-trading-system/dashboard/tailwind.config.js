/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      fontFamily: { mono: ["JetBrains Mono", "Fira Code", "monospace"] },
      colors: {
        brand: { DEFAULT: "#00e5ff", dim: "#0097a7" },
        success: "#00e676",
        danger: "#ff1744",
        warn: "#ffea00",
      },
    },
  },
  plugins: [],
};
