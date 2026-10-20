# Decisions Made

## Design Decisions

1. **Solution is developed making heavy use of LLMs.** Scope of the project is too large to quickly develop by hand, thus this is treated as a POC and an example to build from. [Rick Rubin on taste](https://www.youtube.com/shorts/akcSX81KOv4).
2. **Many decisions were made without thorough analysis of alternatives available**. I treat it as a hackathon project, where time is limited and perfection is impossible so reflection has to happen only at the end. Alternatives are considered after development and documented below.
3. **Focus is on demonstrating data modelling, data quality and system
   design thinking.** Meaning that scraping and ingestion gets less review time (treated more like external service) than dbt, output quality and system design.
4. **Precision is valued over recall (flagged business should be suspicious).** There is huge amount of noise and entropy. Only business that confidently can be identified with public sodra data are classified. (Limitation)
5. **Heuristics for popularity is naive for simplicity.** Actual business of a business is ranked by total reviews, which is a weak metric, especially in month by month analysis.
6. **Future monthly cadence informed data modelling to append.**
7. **A lead needs two signals at once.** Busy: reviews per insured employee at least 2× the category median. Paid little: average wage at most 10% above that year's minimum wage (seed `minimum_wage.csv`; 2026: €1153, so €1268.30). A low wage alone never makes a lead; busy with a normal wage is only "watch".
8. **Companies are compared only within their category.** The median is per category and month, and needs at least 5 comparable companies; otherwise the company is "clear" with "too few comparable companies".
9. **Weak data is excluded before scoring, not dropped.** Weak match, not operational, no Sodra employee data, or too few reviews (200 cafés, 100 hair, 50 car service). Excluded rows stay in the table with their reason.
10. **Every lead explains itself.** A one-sentence reason built from the row's own numbers, plus the website with the company code, the code snippet, the Sodra row and Maps links.
11. **Thresholds are configuration, not code.** dbt vars and a seed, so they can be changed or tried with `--vars` without editing SQL.
12. **Places are rolled up to the company (jar_code).** A chain's reviews are summed and compared against the company's insured employees.
13. **`--top N` is for demo output and never relabels.** It adds the "clear" companies closest to the threshold, still shown as CLEAR.

## Alternatives

1. Using other sources for real activity from google. Oxylabs solution for maps could be good alternative as it would give html of a business with more usable data. Google ToS allows map scraping?
2. Using other open public source. Sodra one is good enough sadly no way to automate it.
3. Avoiding website scraping or utilizing different technique. Code was already written and it worked okay [DD2].
4. Creating leads using a logistic regressor or some statistical test. Would be explainable enough for the analyst, requires more data to build reliably.
5. Outlier tests (z-score, IQR, log scale). Ratio to the category median gives the same order and is easier to explain to an analyst.
6. A single composite score. Rejected: two yes/no signals are easier to justify than a weighted number.
7. Review velocity (new reviews per month). Better activity signal, but needs stored review history (ToS, see Limitations 3).

## Limitations

1. Data from sodra has to be uploaded manually, found no way to curl it.
2. Shadowy activities would involve such things as having no website/no public jar code, dealing exclusively with individuali veikla. These strong signals are not in scope for this.
3. Google places api stores data, which is against their ToS. Actual solution should always requery their api (it might be possible to store calculated metrics). This makes in depth month by month analysis less feasible. It is still possible to compare flagged companies. The public repo does keep one demo snapshot (1,248 Places results) so the demo runs without keys.
4. Repeat scraping runs will generate duplicate rows (not leads) in the database.
5. Selection bias: only businesses that publish their company code online can be matched (154 of 1,248 places).
6. Sodra counts insured employees company-wide and leaves out self-employed people; average wage is hidden for small companies; data lags 1–2 months.
7. Review counts are lifetime totals; tourist places collect more reviews per customer; hair stylists often rent chairs as self-employed.
8. Few peers: car service and hair and beauty have only 6 comparable companies each on the demo data, just above the minimum of 5, so their medians are fragile.
9. Changing a threshold re-judges every past month (the minimum wage is per year, from a seed).
10. No ground truth and no validation: thresholds are not sensitivity-tested and match precision has not been hand-checked, so neither the stability of the list nor the correctness of the matches is measured. Nothing shows that a flagged business evades tax.
