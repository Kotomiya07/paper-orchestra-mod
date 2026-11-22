# Synthetic experimental log

The fixture contains 100 synthetic headers and uses a fixed seed of 7.
All conditions use the same headers and classifier. Coverage is the fraction
of headers accepted; accuracy counts only accepted predictions.
The values below are illustrative, not measured results.

| Policy | Coverage | Accuracy on accepted predictions |
| --- | ---: | ---: |
| Single pass | 100% | 82% |
| Two of three agree | 90% | 90% |
| Three of three agree | 75% | 96% |
