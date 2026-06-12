/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,ts,jsx,tsx,mdx}"],
  theme: {
    extend: {
      colors: {
        // HealTeam palette (W1 placeholder; design task in W2 may refine).
        heal: {
          50:  "#f4f7fb",
          100: "#e3ecf6",
          500: "#3b6cb1",
          700: "#234a7d",
          900: "#0e2647",
        },
      },
    },
  },
  plugins: [],
};
