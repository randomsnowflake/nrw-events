PYTHON ?= python3
VENUE_FEED ?=
VENUE_AUDIT ?= /tmp/nrw-events-venue-audit.json

.PHONY: venue-audit venue-registry-check

venue-audit:
	@test -n "$(VENUE_FEED)" || (echo "Set VENUE_FEED to an importer JSON snapshot" >&2; exit 2)
	$(PYTHON) scripts/audit_venue_locations.py "$(VENUE_FEED)" --output "$(VENUE_AUDIT)"

venue-registry-check:
	$(PYTHON) scripts/build_verified_venue_locations.py \
		scripts/venue_geocoding_proposals.json \
		--registry scripts/nrw_events/verified_venue_locations.json \
		--decisions /tmp/nrw-events-venue-decisions.json \
		--check
