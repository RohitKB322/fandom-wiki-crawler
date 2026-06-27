# Fandom Game Wiki Crawler

A small Python crawler that starts from one Fandom wiki page, follows links on the same wiki, and saves structured JSON for each page.

It extracts:

- page title
- summary text
- Fandom portable infobox fields
- sections and section text
- categories
- images
- same-wiki links

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
python fandom_crawler.py "https://minecraft.fandom.com/wiki/Diamond" --max-pages 25 --delay 1.5 --output output
```

Results are saved like this:

```text
output/
  index.json
  pages/
    Diamond.json
    ...
```

## Notes

- The crawler only follows pages on the same Fandom subdomain as the starting URL.
- It skips common non-article namespaces such as `Special:`, `File:`, `Category:`, `Template:`, and `User:`.
- Use a delay of at least 1 second so the crawler behaves politely.
- Large wikis can contain thousands of pages, so start with a low `--max-pages` value and increase it only when the output looks right.

## Streamlit App

Run the user-friendly web app locally:

```powershell
streamlit run app.py
```

The app lets you paste a Fandom URL, choose how many pages to fetch, choose JSON or paragraph text, preview the result, and download the file.

For Streamlit Cloud, upload this folder to a GitHub repository and set the app entry file to:

```text
app.py
```
