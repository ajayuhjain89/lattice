import next from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import jsxA11y from "eslint-plugin-jsx-a11y";

const config = [
  ...next,
  ...nextTs,
  {
    rules: {
      ...jsxA11y.configs.strict.rules,
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_", destructuredArrayIgnorePattern: "^_" }],
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXOpeningElement[name.name='div'] > JSXAttribute[name.name='onClick']",
          message: "Use a <button> or a link; never a clickable div.",
        },
      ],
    },
  },
  { ignores: [".next/**", "src/api/schema.gen.ts", "playwright-report/**", "test-results/**", "next-env.d.ts"] },
];

export default config;
