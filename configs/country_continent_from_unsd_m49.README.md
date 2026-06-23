# CountryCode to Darwin Core-style continent mapping

This file is generated from `configs/country_region_unsd_m49.csv`, which is parsed from the official UNSD M49 overview table.

Important distinction:

- `unsd_regionName`, `unsd_subRegionName`, and `unsd_intermediateRegionName` are parsed from the official UNSD M49 overview.
- `continent` is a derived Darwin Core-style continent candidate produced by a documented policy.
- The policy is needed because UNSD uses `Americas`, while Darwin Core examples use `North America` and `South America`.

Policy:

- UNSD Region Name Africa -> Africa
- UNSD Region Name Asia -> Asia
- UNSD Region Name Europe -> Europe
- UNSD Region Name Oceania -> Oceania
- UNSD Region Name Antarctica -> Antarctica
- UNSD Americas + South America -> South America
- UNSD Americas + Northern America / Central America / Caribbean -> North America

Rows with invalid, blank, unknown, or non-standard input countryCode should remain review-only in the enrichment audit.
