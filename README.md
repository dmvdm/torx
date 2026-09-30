# Test Observer Reports eXporter

This script exports test results from Test Observer to the reports rendered by Allure

How to use:

- Install allure: npm install allure
- Run the export: export_artefact_allure_test_results.py --api-url <api-url> --token <api_token> --artefact <artefact> --family <family> --latest-only --from-date 2026-09-01
- Generate Allure report: npx allure generate test-results -o test-report
- single index.html is ready to be served
