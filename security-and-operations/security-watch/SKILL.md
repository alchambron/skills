---
name: security-watch
description: Weekly EnerZam security watch. Lists the week's major published vulnerabilities, checks JouleMV and WebSynco exposure, and produces an English and French report as T3 HTML and PDF.
disable-model-invocation: true
---

# EnerZam weekly security watch

Answer one question for the past 7 days: did any vulnerability in the news put JouleMV, WebSynco or EnerZam IT at risk, and what does EnerZam do about it. The deliverable is the same report in English and French, shown in T3 and printed to PDF.

## Paths

- Archive: `~/Documents/Projects/ISMS/security-watch/`. One run directory per issue date (`YYYY-MM-DD/`) holding `report-en.html`, `report-fr.html`, `meta.json` and the PDFs.
- IT facts the repos cannot show: `~/Documents/Projects/ISMS/security-watch/it-inventory.md`.
- Repos: JouleMV `~/Documents/Projects/Joule_MV/JouleMV`, WebSynco `~/Documents/Projects/WebSynco/WebSynco`. Read both at `origin/work` after `git fetch origin`, so local checkout state never matters. The shell is zsh: write `git show "${REF}:path"`, never `$REF:path` (zsh reads `:w`, `:h` and similar as modifiers).
- PDF builder: `scripts/build_pdf.py` in this skill.

## Steps

### 1. Open the run

Window is the 7 days ending today. Create today's run directory and copy the latest previous run's `report-en.html`, `report-fr.html` and `meta.json` into it as the template. Read `it-inventory.md` and the previous report's Actions section.

Done when: the run directory exists with the three template files, and the previous run's open actions and verify items are listed in your notes.

### 2. Collect the news

Search the window for: new CISA KEV entries, actively exploited zero-days, vendor security releases for products in the EnerZam stack (Tomcat, Java/Oracle CPU, Spring, PostgreSQL, nginx, Node.js, npm supply chain, Docker, OpenSSL, Linux, Jenkins, GitHub Actions), and anything with CVSS 9+ that the security press covered widely. Run searches in parallel.

Keep an item when it is exploited in the wild, or rated critical, or touches the stack. For each kept item record date, product, CVE(s), severity, exploited or not, fixed versions and one source URL. Confirm the facts that drive a verdict (affected versions, fixed version, attack preconditions) on the vendor's own advisory page; trackers often disagree on scores and numbers.

Done when: every kept item has a vendor-confirmed affected range and preconditions, or is marked as press-only in the notes.

### 3. Check exposure

Refresh the stack versions from the repos each week: JouleMV `cicd/production/Dockerfile.deploy`, `pom.xml`, `frontend/package.json` and lockfiles, `frontend/cicd/production/Dockerfile.deploy`, `cicd/preview/gateway/Dockerfile`, compose files and workflows; WebSynco `websynco/Dockerfile`, `websynco/pom.xml`, `websynco-request-module/pom.xml`, Jenkinsfiles.

Give every news item exactly one verdict per affected party, using the attack's precondition, not just the version. A version inside the affected range with the precondition absent (connector, protocol, realm, API or runtime the code never uses) is **Patch**, not exposed. Prove absence with a source search and name the evidence in the report.

| Verdict | Meaning |
| --- | --- |
| Exposed | Affected version and precondition present. Stop and tell the user immediately, before finishing the report. |
| Patch | Affected version, precondition absent. Fix at the next release. |
| Verify | Cannot be decided from the repos (external base image, a server, an IT asset). |
| Not affected | Product present, this flaw does not apply. |
| Not used | Product absent from EnerZam. Mark "IT confirmed" only when `it-inventory.md` says so. |

Done when: every kept item has a verdict for JouleMV, WebSynco and IT, each backed by a file or inventory line, and every previous Verify item is either resolved or carried forward.

### 4. Write the report

Edit `report-en.html` in place, keeping the template's structure, classes and CSS: verdict box with tiles, (1) news table, (2) exposure by product cards, (3) actions, (4) how to explain it, method and sources. Sections 2 and 3 keep `class="page-start"`. Closed actions from last week stay one run as `li class="done"`, then drop.

Wording rules:
- Name the company **EnerZam**. Write "affects EnerZam", "EnerZam checked", never first person (no us, we, our; in French no nous, notre, on).
- The news table's last column header is `EnerZam` with `style="text-transform:none"`.
- Section 4 is plain language for management or a client: what happened, whether EnerZam is affected, what EnerZam will do.

Then write `report-fr.html` as a full translation in Quebec French ("TI", "manchettes", "rançongiciel", "s.o."), French dates ("22 oct. 2026"). Product names, CVE numbers, versions and paths stay as is. Update `meta.json`: `rev` back to 1 for a new issue, period, issued date, PDF file names.

Done when: both files carry the same items, counts and verdicts, and a search of their visible text finds no first-person word.

### 5. Show it in T3

Preview `report-en.html` with `html_preview`, fix anything broken, then publish it with `html_render` (height = preview contentHeight, capped at 2000). The French version is rendered only if the user asks.

### 6. Build and check the PDFs

```bash
python3 -I ~/.claude/skills/security-watch/scripts/build_pdf.py ~/Documents/Projects/ISMS/security-watch/<date>
```

Read every page of both PDFs back. Fix and rebuild until no product card splits across pages, no heading sits alone at a page bottom, and the French text has no line starting with ":" or ";". Shorten text before shrinking fonts.

Done when: both PDFs pass that read-back.

### 7. Report back

Give the user the two PDF paths, anything **Exposed** first, new Verify items, what changed since last week, and the questions only IT can answer. When the user confirms an IT fact, add it to `it-inventory.md` with the date, then update the report and rebuild.
