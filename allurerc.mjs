import { defineConfig } from "allure";

export default defineConfig({
  name: "Test Report",
  output: "./test-report",

  categories: [
    {
      name: "Failed tests",
      matchers: { statuses: ["failed"] },
      groupBy: [{ label: "epic" }, { label: "feature" }],
      groupByMessage: true,
      expand: true,
    },
    {
      name: "Skipped tests",
      matchers: { statuses: ["skipped"] },
      groupBy: [{ label: "epic" }, { label: "feature" }],
      expand: false,
    },
    {
      name: "Passed tests",
      matchers: { statuses: ["passed"] },
      groupBy: [{ label: "epic" }, { label: "feature" }],
      expand: false,
    },
  ],

  plugins: {
    awesome: {
      options: {
        reportLanguage: "en",
        singleFile: true,
      },
	stepTreeExpansion: "collapsed"    
    },
  },
});
