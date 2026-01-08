from typing import Optional

from sqlalchemy.orm import Session

from app.models import DocumentAnalysisCache


def get_cached_analysis(
    db: Session,
    document_hash: str,
    scoring_model_sha: Optional[str],
    pipeline_git_sha: Optional[str],
) -> Optional[DocumentAnalysisCache]:
    if not document_hash:
        return None
    query = db.query(DocumentAnalysisCache).filter(
        DocumentAnalysisCache.document_hash == document_hash
    )
    if scoring_model_sha:
        query = query.filter(DocumentAnalysisCache.scoring_model_sha == scoring_model_sha)
    if pipeline_git_sha:
        query = query.filter(DocumentAnalysisCache.pipeline_git_sha == pipeline_git_sha)
    return query.order_by(DocumentAnalysisCache.updated_at.desc().nullslast(), DocumentAnalysisCache.id.desc()).first()


def upsert_analysis_cache(
    db: Session,
    document_hash: str,
    scoring_model_sha: Optional[str],
    pipeline_git_sha: Optional[str],
    detected_points: Optional[dict],
    scoring_result: Optional[dict],
    ai_analysis: Optional[dict],
) -> DocumentAnalysisCache:
    cache = db.query(DocumentAnalysisCache).filter(
        DocumentAnalysisCache.document_hash == document_hash,
        DocumentAnalysisCache.scoring_model_sha == scoring_model_sha,
        DocumentAnalysisCache.pipeline_git_sha == pipeline_git_sha,
    ).first()
    if cache is None:
        cache = DocumentAnalysisCache(
            document_hash=document_hash,
            scoring_model_sha=scoring_model_sha,
            pipeline_git_sha=pipeline_git_sha,
        )
        db.add(cache)
    cache.detected_points = detected_points
    cache.scoring_result = scoring_result
    cache.ai_analysis = ai_analysis
    return cache
