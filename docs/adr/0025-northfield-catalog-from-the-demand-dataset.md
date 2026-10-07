# 0025. Northfield's catalog: the demand dataset's top products, renamed in a committed file

- **Status:** Accepted
- **Date:** 2026-10-07

## Context

Northfield Supply, the main demo tenant, sells industrial and office supplies (brief §1). Its demo
catalog has about 300 products (brief §8), and `demand-forecast` will forecast demand for the same
products, so a forecast must map one to one onto a Pricewright product (portfolio decision D-04).

`demand-forecast` uses Kaggle's
["Forecasts for Product Demand"](https://www.kaggle.com/datasets/felixzhao/productdemandforecasting):
about a million order lines of a real manufacturer from 2011 to 2017, for 2,160 products in 33
categories, all encoded (`Product_1359`, `Category_019`), without names or prices. It is licensed
under GPL-2, so it is downloaded by script and never committed. Kaggle serves public datasets
through its API without credentials
([open access for public dataset downloads](https://www.kaggle.com/product-announcements/485439);
checked 2026-10-07), so the download needs no token.

Looking at the data (checked 2026-10-07): the 300 products with the most demand belong to 8
categories, 192 of them to `Category_019`. Quantities are measured in each category's own units, so
one category of small items dominates any ranking by volume, and the brief's "about 10 categories"
was an estimate made before anyone looked.

## Decision

- **Selection:** the 300 products with the highest total demand, returns (written in parentheses)
  subtracted, ties to the lower code. Every top-N that `demand-forecast` models (its brief
  suggests 200) is then inside the catalog. The result has 8 categories.
- **A committed mapping file,** `src/pricewright/demo/data/northfield_catalog.csv`: per product, its
  dataset code and category, then Northfield's SKU, name, category, unit of measure, list price and
  unit cost. It holds no demand figure, date or warehouse, and its rows are sorted by code, so not
  even the ranking shows. The seed reads only this file: it never needs the dataset.
- **Names and prices are synthetic and deterministic.** `scripts/build_northfield_catalog.py` reads
  the downloaded dataset, picks the products and describes each one with a random generator seeded
  with the product's own code. A product keeps its name whichever others are selected, and the
  generator uses only `random()`, the one method whose sequence Python keeps across versions
  ([notes on reproducibility](https://docs.python.org/3/library/random.html#notes-on-reproducibility)).
  Names come from per-category templates of generic industrial and office supplies, with no brand
  names; SKUs keep the dataset's number (`FST-1359`); list prices are in cents and margins vary by
  category, thinner on paper than on fasteners.
- **The file and the script stay in step:** a test rebuilds the file from its own codes, so the file
  is exactly what the script makes, and another loads every row through the product rules.

## Alternatives considered

- **The 280 top products plus the leaders of two more categories,** or **about 30 products from each
  of the top 10 categories:** closer to "about 10 categories" and more balanced, but the forecasting
  project's top-N would no longer be inside the catalog, or would need a selection rule of its own.
- **A fully synthetic catalog** (the brief's fallback): forecasts would not map onto real demand
  histories.
- **Faker for names:** a new dependency whose seeded output
  ["is not guaranteed to be consistent across patch versions"](https://faker.readthedocs.io/en/master/),
  and no vocabulary for industrial supplies; its company names could also match real companies.
- **Committing the dataset or its ranking:** the data's license and size; the ranking is demand data
  in disguise.

## Consequences

- **Positive:** `demand-forecast` maps its forecasts by dataset code with the same file; the seed
  and CI never download anything; the catalog is reproducible from a public download and one
  command.
- **Negative:** one category holds 192 of the 300 products (fasteners, which in industrial
  distribution are the category with the most SKUs anyway), and the smallest has a single product;
  synthetic prices carry no signal for forecasting, which does not use prices; the dataset's units
  of measure are unknown, so a "box of 100" is an assumption of the demo.
