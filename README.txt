issue-dat-artifact depends on the following python modules:
- Requests
- lxml
- BeautifulSoup

The script takes an optional json file as an argument, which serves
as a translation table from SourceForge categories and groups to
GitHub labels, as well as for mapping usernames between the two
systems.

There is such a file included in this repository (tr.json), which
served for migration of SuperCollider bugs.
