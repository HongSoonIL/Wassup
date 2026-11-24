"""A continuation points to the edition the author and editor actually saw."""

from .publication_models import FeedPost


def current_previous(post, now):
    """Resolve one hop on a freshly fetched post; callers authorize the current post."""
    if not post.previous_post_id or not post.previous_version:
        return None
    previous = post.previous_post
    if (
        previous.pk != post.pk
        and previous.character_id == post.character_id
        and previous.version == post.previous_version
        and previous.state == FeedPost.State.PUBLISHED
        and previous.published_at is not None
        and previous.published_at <= now
        and previous.published_at < (post.published_at or post.publish_at)
        and previous.photo_id
        and previous.photo.character_id == previous.character_id
        and previous.photo.image
    ):
        return previous
    return None
