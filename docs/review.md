# Project review — 4 November 2026

## Scope and evidence

Reviewed the local setup, CLI, artifact storage, application routes, bootstrap, documented model/funding contracts, CI configuration and user workflows. Ran the complete backend suite, TypeScript/Vite build, and Chromium acceptance suite. This is a local review, not a certification that every possible defect has been found. Existing staged and unstaged project changes were preserved.

## Findings

| Priority | Problem / consequence | Status or next action |
| --- | --- | --- |
| High | Bare `uv sync` omits ML extras, producing `No module named sklearn` during bootstrap. | README now gives explicit extras installation and uses the installed virtual environment directly. Bootstrap now provides an actionable dependency error. |
| High | Beginners could confuse quick demo with trained, complete workflow. | README explains four-policy quick replay versus five-policy bootstrap with risk/timing training. |
| High | Model probabilities could be interpreted as the risk after applying the selected funding policy. | README explicitly identifies fixed-zero reference-policy predictions. A future UI review should assess whether this distinction is prominent enough at every probability display. |
| High | Synthetic calibration can be mistaken for real-world accuracy. | Documented prominently. Real settlement data validation remains outstanding. |
| High | Smoke test has only two final test days; many snapshots do not create independent evidence. | Documented. Expand day cohorts and repeat seeds before drawing research conclusions. |
| Medium | Old documentation called the implemented funding scenario engine future work and omitted the existing universal lock file. | Corrected model and implementation-status guides. |
| Medium | Users did not have a complete walkthrough or evidence collection procedure. | Added dashboard walkthrough, storage/API guide, metric interpretation, validation commands and presentation checklist to README. |
| Medium | Monthly demo depends on today's date; seed alone does not reproduce it across months. | Explained saved-scenario replay and retained configuration/rates as the reproducible research path. |
| Medium | Startup and changed evaluations rely on external FX/time services. Automated tests mock these services. | Dependency and troubleshooting documented. Live provider availability is not established by the test suite. |
| Medium | Empirical timing uses assumed shared-quantile dependence and conservative missing-receipt handling. | Explicit research limitation; calibrated timing classifiers and estimated dependence remain future work. |
| Medium | LP planning objective differs from realised costs; optimizer may not outperform baselines. | Explained. Compare matched replay outcomes and report negative results honestly. |
| Medium | Daily cash episodes and full reserve draws simplify treasury operations. | Rolling cash management, source-currency ledger completion and realistic repayment policies remain future work. |
| Medium | Securities availability assumed; no partial settlement, netting or external custody integration. | Scope limitation, not claimed as built. |
| Medium | Local service lacks shared-user authentication and distributed worker infrastructure. | Keep local deployment scope; design these before public deployment. |
| Medium | CI installs Python dependencies through pip rather than enforcing `uv.lock`. | Remaining reproducibility improvement; remote CI was not run in this review. |
| Low | Production JS bundle is about 777 kB before gzip. | Build succeeds; route/chart code splitting remains a performance improvement. |
| Low | Build emits Radix `use client`/sourcemap warnings; test client emits an httpx deprecation warning. | Nonblocking in the verified environment; resolve through deliberate dependency maintenance. |
| Low | Prior verification documents contain historical test counts. | Treat this dated record as the current local check; historical counts describe earlier milestones. |

## Checks completed

- Backend: **89 passed**, including clean bootstrap with training, five policy runs, maturity checks and artifact validation. One Starlette/httpx deprecation warning.
- Frontend: TypeScript check and Vite production build passed. Bundle/directive warnings remain as above.
- Browser: **14 passed**, covering trade detail, funding jobs, reload recovery, error handling, mobile navigation, both themes, custom dropdowns, sidebar sizing, calendar bounds, online-date constraints and evaluation reruns.
- Whitespace: `git diff --check` passed.

Backend and browser tests use controlled rates/time. No new real-provider training run or external financial validation was performed in this review. The dependency-error improvement was also exercised by simulating an unavailable bootstrap import; it reports the install command rather than an unexplained missing-module message.

## Recommended college-project acceptance boundary

A defensible interim/final demonstration can show reproducible synthetic generation, chronological model training, an explained prediction, five matched funding policies, a completed background recomputation, saved evidence and passing tests. Present the application as a local research prototype. A research paper additionally needs a stated hypothesis, larger repeated experiments, suitable baselines, uncertainty estimates and honest limitations. Neither a polished dashboard nor passing software tests establishes research validity.
