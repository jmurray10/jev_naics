# Diagrams

Pictures of how the classifier works. Each has a PNG (for slides and chat), an SVG (sharp at any
size), and the Mermaid source (`.mmd`) both were rendered from. Dashed boxes are model (Jev) calls;
everything else is code.

| Picture | Shows |
|---|---|
| [01-peas-agent](01-peas-agent.png) | The classifier as a PEAS agent: environment, sensors, the closed list, the gates, and the two actuators |
| [02-search-method](02-search-method.png) | Search: spelling and terms, top 10, one Jev pick, gates |
| [03-tree-method](03-tree-method.png) | Tree walk: Jev per level, 3 finalists, one pick, gates |
| [04-hybrid-method](04-hybrid-method.png) | Hybrid: search first, the tree walk as a fallback, and when it never falls back |
| [05-data-flow](05-data-flow.png) | Census files to the tree JSON and Postgres, then Jev and the gates |
| [06-one-description-end-to-end](06-one-description-end-to-end.png) | For non-technical readers: one real description (the retail bakery) followed from typing to answer, with the actual numbers at each step. Yellow is the person, green is code and the database, blue dashed is Jev. |

## Re-rendering

Edit the `.mmd` file, then from the repo root (needs Node and a local Chrome):

```
PUPPETEER_SKIP_DOWNLOAD=1 npx -y @mermaid-js/mermaid-cli \
  -p puppeteer.json -i docs/diagrams/01-peas-agent.mmd -o docs/diagrams/01-peas-agent.png -b white -s 2
```

`puppeteer.json` is `{"executablePath": "<path to Chrome>", "args": ["--no-sandbox"]}`. Use `.svg`
as the output extension for the SVG. Keep the Mermaid block in `PEAS.md` in sync with
`01-peas-agent.mmd`.
