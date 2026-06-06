"""Shared fixtures and Hypothesis configuration for ScrapeBot tests."""

from hypothesis import settings as hyp_settings

# Configure Hypothesis to run 100 examples per property test
hyp_settings.register_profile("default", max_examples=100)
hyp_settings.load_profile("default")
