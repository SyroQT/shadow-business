# Decisions Made

> [What the future of engineering looks like](https://www.youtube.com/shorts/akcSX81KOv4)

## Design Decisions

1. **Solution is developed making heavy use of LLMs.** Scope of the project is too large to quickly develop by hand, thus this is a POC and an example to build learn and iterate from, please look at it through this lens.
2. **Core hypothesis is that shady business have a lot of traffic (proxy reviews) but pay close to minimum wage while extra income is undeclared**
3. **Many decisions were made fast**. I treated it as a hackathon project, where time is limited and perfection is impossible. So iteration has to happen only at the end. Alternatives are considered after development and documented below.
4. **Focus is on demonstrating data modelling, data quality and system design thinking.** I prioritized actual downstream data work at a cost of data generation.
5. **Precision is valued over recall (flagged business should be suspicious).** Domain has huge amount of noise and entropy. Only business that confidently can be identified with public sodra data are classified. Thus prioritizing end analysts time.
6. **Future need for monthly runs means data appends.**
7. **Weak data is excluded from scoring as leads but kept** In case of data quality (no sodra employee or low reviews) rows stay so analyst could query them.
8. **For lead to be flagged two binary metrics have to be met** One side comes from places data, other side comes from declared wages. Busy: reviews per insured employee at least 2× the category median. Low wage: average wage at most 10% above the minimum wage.
9. **Was afraid that no flagged business will be found so added extra categories**
10. **Low wage does not generate a lead** A busy company with a normal or unpublished wage is `watch`: kept visible, ranked below `flag`.
11. **Companies are compared only within their category.** The median is per category and month, and needs at least 5 comparable companies; otherwise the company is "clear" with "too few comparable companies".
12. **Every lead has text explanation.** A one-sentence reason built from the row's own numbers, plus the website with the company code, the code snippet, the Sodra row and Maps links.
13. **Places are rolled up to the company (jar_code).** To handle chains: reviews are summed and compared against the company's insured employees.

## Alternatives

1. Using other sources for real activity from google. Oxylabs solution for maps could be good alternative as it would give html of a business with more usable data. Google disalows of map scrapping so that would be an issue.
2. Using other open public source. I think same data should be reachable by code somehow/somewhere.
3. Avoiding website scraping or utilizing different technique for joining to sodra. Code was already written and it worked okay [DD3].
4. Creating python service to evaluate leads and write to final mart. Decided it was too complicated for the case.
5. Creating leads using a logistic regressor or some statistical test to find outliers. Would still be explainable enough for the analyst, requires more data to build reliably.
6. A single number score. Easy to rank and prioritize, would be hard for explainability.
7. Review velocity (new reviews per month). Better activity signal, but needs stored review history (ToS).
8. Using busy time histogram from maps as busy signal.

## Limitations

1. This became quite a heavy project in complexity and multiple dependencies. Will be hard to reason about and refactor.
2. Shadowy activities would involve such things as having no website/no public jar code, dealing exclusively with individuali veikla. These strong signals are not in scope for this.
3. Only businesses that publish their company code online can even be evaluated. This removes a lot of potential leads.
4. Chain based companies are rolled up but follow the same logic, the fact that it's a chain I think is strong signal against shady practises but it's not addressed.
5. It's impossible to evaluate if current approach flags are shady business. They as well could be relatively small and exceptional ones.
6. Currently I store data from Google places api, which is against their ToS. Actual solution should always requery their api (it might be possible to store calculated metrics but). This makes in depth month by month analysis less feasible. It is still possible to compare flagged companies.
7. Future need of month by month analysis is partly met as that would require monthly data from Google places but they provide snapshot in time only.
8. Data from sodra has to be uploaded manually, each month.
9. Sodra counts insured employees company-wide and leaves out self-employed people, average wage is hidden for small companies and data from them lags 1–2 months.
10. Medians that are not robust: car service and hair and beauty have only 6 comparable companies each on the demo data, just above the minimum of 5. High deviation of data.
11. A lot of business logic is hard-coded inside SQL, in my opinion other service should handle it.
