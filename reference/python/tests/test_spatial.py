"""Shared radius conformance and invalid point predicates."""
import json
from pathlib import Path
import pytest
from gezk import Catalog, verify_and_extract
from gezk.spatial import distance_meters, validate_location, validate_radius

KIT = Path(__file__).resolve().parents[3] / 'conformance'
VECTORS = json.loads((KIT / 'vectors.json').read_text())

def test_distance_vectors():
    for vector in VECTORS['spatial']['distance']:
        assert distance_meters(vector['from'], vector['to']) == pytest.approx(vector['expectedMeters'], abs=1e-7)

def test_radius_vectors_and_scope(tmp_path):
    verify_and_extract(KIT / VECTORS['fixture']['path'], tmp_path / 'catalog')
    catalog = Catalog(tmp_path / 'catalog')
    try:
        for probe in VECTORS['spatial']['nearby']:
            page = catalog.nearby_documents(probe['radius'])
            assert [doc['id'] for doc in page['documents']] == probe['expectedDocumentIds']
            assert page['total'] == len(probe['expectedDocumentIds'])
        radius = VECTORS['spatial']['nearby'][0]['radius']
        doc = catalog.get_document('doc-0000')
        assert doc['locations'][0]['id'] == 'seattle'
        assert all(hit.document_id == 'doc-0000' for hit in catalog.search_documents(doc['title'], limit=1, spatial=radius))
        assert all(hit.document_id == 'doc-0000' for hit in catalog.search_chunks(doc['title'], limit_per_shard=1, spatial=radius))
        assert not [check for check in catalog.validate(deep=True) if not check[1]]
    finally: catalog.close()

@pytest.mark.parametrize('radius', [
    {'latitude': 91, 'longitude': 0, 'radiusMeters': 1},
    {'latitude': 0, 'longitude': 0, 'radiusMeters': -1},
    {'latitude': 0, 'longitude': 0, 'radiusMeters': float('nan')},
])
def test_invalid_radius(radius):
    with pytest.raises(ValueError): validate_radius(radius)
