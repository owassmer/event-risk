# Data: how to search

Three searches bring the world in: a company's own record, the web, and the venues' markets. Each returns what its source holds, in the source's own words, and is recorded with what it returned.

## A company's record

`search_company(ticker, kind, params)` reads one kind of a company's own record, the newest first. `ticker` is one of the company's US tickers, or its name's id, which is how a company listed only abroad is named. A kind reads from the company's listing to the as-of, up to its `limit`. `more` says there are more, and dates narrow it to what your question needs. Each document in a list is held as a page: `get(id)` reads it whole, `get(id, text)` reads the passages that contain `text`, and citing it keeps it as a `Source`.

- `news`: articles about the company, each with its full text where it has one. `published.gte` and `published.lte`, a date or a UTC time; `channels` and `tags`, the publisher's topics; `limit`, 100 when not given.
- `current_reports`: what the company reports between its annual reports, as it happens: the items of its 8-Ks, or a foreign issuer's 6-Ks. `filing_date.gte` and `filing_date.lte`; `limit`, 10 when not given.
- `annual_report`: its annual report. A 10-K comes section by section, each whole: `business`, `risk_factors`, and `mda`, the management's discussion and analysis. `section` names one, a filing can lack a section, and `mda` is held for recent filings. A foreign issuer's 20-F or 40-F comes whole, the latest when no limit is given. `filing_date.gte` and `filing_date.lte`; `limit`, 10 sections when not given.
- `risk_factors`: each risk factor in its 10-Ks, with its category and its text. `filing_date.gte` and `filing_date.lte`; `limit`, 100 when not given. A foreign issuer's risk factors are in its annual report.
- `earnings`: each result reported or scheduled, with the consensus estimates and the surprise. The reports scheduled next come first. `date.gte` and `date.lte`; `date_status`, `projected` or `confirmed`; `fiscal_year` and `fiscal_period`; `limit`, 100 when not given.
- `guidance`: each range the company gave for a period, with its earlier range and the consensus then. `date.gte` and `date.lte`; `fiscal_year` and `fiscal_period`; `limit`, 100 when not given.
- `ratings`: the analysts' consensus over the ratings given between its dates: how many gave each rating, and the consensus, high and low price targets. With no `date.gte` it runs from the company's listing, so give the window the consensus is over.

A company listed only abroad has two kinds, `earnings` and `ratings`, read from its own market: its results reported by the as-of, each quarter once, and its ratings as they stand on the day they are read. `params` does not apply to them.

## The web

`search_web(text, params, proposition_id)` is one web search for `text`. It returns each page's address, title and date, with the passages quoted from it. Given a proposition, the search is recorded on it. `params` are the search's own:

- `max_uses`: how many queries it may run, 3 when not given;
- `allowed_domains` or `blocked_domains`, one of the two: domains without a scheme, with a path where one narrows it, as `example.com/blog` does;
- `user_location`: `{"type": "approximate", "city", "region", "country", "timezone"}`, with `country` a two-letter code and `timezone` an IANA zone.

`fetch_source(url, text)` reads a page whole and returns its id, its title, the date it speaks as of, its length and an excerpt, and, given `text`, the passages that contain it. A passage you quote is the page's own words, so quote from what it returns; `get(id, text)` reads the passages that contain other words.

## The venues' markets

`search_markets(text, proposition_id)` searches the venues for markets that bear on a proposition, passing your words to each venue's own search. Each venue ranks what it lists, and the search returns each venue's ten most relevant families that have a market open at the as-of, the venues taking turns. A family of up to twelve open markets lists them, each with its id, question, close date, bid, ask, price and depth. A larger family comes as how many it holds, each market's question in its venue's words, and the span of their closes; a ladder of strikes comes as its first and last question and its strikes. `get` reads the family whole, with each book.

- A venue's search matches a market's own words. Search in the words a market's question would use, with the date where the event has one.
- A search has one page. A narrower text reaches what a broader one did not return.
- `get(family id)` reads a family whole, with every market in it that is open at the as-of. A family names the family that holds it (`within`), where a venue files each date of one question as a family of its own, and `get` of that id reads the families in it.
- `get(market id)` reads one market: its terms, its book, the range its price allows, its last trade and its history (3.3).
- The search is recorded on the proposition it is for, and the event shows how many searches stand behind its markets.
