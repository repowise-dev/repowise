"""Runtimes that place components a metadata file registers: Salesforce, LWC, Android."""

from __future__ import annotations

from .base import FrameworkFacts

#: The Salesforce org, which places what flexipages, quick actions, tabs,
#: email services and Experience sites name.
SALESFORCE = FrameworkFacts(name="Salesforce", anchor="framework:salesforce")

#: The runtime that starts the components an ``AndroidManifest.xml`` registers.
ANDROID = FrameworkFacts(name="Android", anchor="framework:android")

#: The Lightning runtime that places the LWC bundles a ``.js-meta.xml`` exposes.
LWC = FrameworkFacts(name="Lightning Web Components", anchor="framework:lwc")
