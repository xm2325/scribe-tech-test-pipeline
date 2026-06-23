# Official UNSD M49 country/region mapping

Generated from the United Nations Statistics Division M49 overview page.

Source URL:
https://unstats.un.org/unsd/methodology/m49/overview/

Downloaded UTC:
2026-06-23T11:19:31+00:00

Files:

- `configs/raw/unsd_m49_overview.html`: raw downloaded official HTML snapshot.
- `configs/unsd_m49_overview_official.csv`: parsed official M49 overview table.
- `configs/country_region_unsd_m49.csv`: simplified countryCode -> UNSD region/subregion mapping.

Important interpretation:

- `unsd_regionName` is the official UNSD macro-geographical region.
- This is not always exactly the same as Darwin Core's `continent` examples.
- UNSD uses `Americas`; Darwin Core examples use `North America` and `South America`.
- Therefore this file should be treated as official geography evidence. Any conversion into Darwin Core-style `continent` should be documented as a separate policy step.
