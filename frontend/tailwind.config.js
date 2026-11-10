// Tailwind 4 loads this explicitly through @config in src/globals.css.
// Theme-dependent colors live in globals.css so Radix portals share the theme.
export default {
  theme: {
    extend: {
      fontFamily: {
        sans: ['var(--font-body)'],
        mono: ['var(--font-code)'],
      },
      fontSize: {
        xs: ['var(--type-caption)', { lineHeight: '1.5' }],
        sm: ['var(--type-body)', { lineHeight: '1.5' }],
        base: ['var(--type-lead)', { lineHeight: '1.5' }],
        xl: ['var(--type-section)', { lineHeight: '1.4' }],
        '2xl': ['var(--type-metric)', { lineHeight: '1.3' }],
        '3xl': ['var(--type-title)', { lineHeight: '1.2' }],
      },
    },
  },
};
