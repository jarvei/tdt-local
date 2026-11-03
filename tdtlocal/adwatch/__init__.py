"""Ad-break detection, ported from the operator's ``tdt-datos`` project.

``hls``/``dash`` read the manifests, ``scte35`` decodes the markers, ``id3`` reads Mediaset's
``admode``, and ``engine`` follows one channel in the background and keeps its state (programme /
ad break) the way the TDT Spain app's ``AdMute`` does.
"""

from .engine import MEDIASET_LAG_S, Vigilance

__all__ = ["MEDIASET_LAG_S", "Vigilance"]
