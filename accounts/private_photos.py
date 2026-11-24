from io import BytesIO
from pathlib import Path
from struct import error as StructError
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, storages
from django.http import FileResponse, Http404
from django.utils.cache import add_never_cache_headers
from django.utils.deconstruct import deconstructible
from PIL import Image, ImageOps
from storages.backends.s3 import S3Storage


@deconstructible
class PrivatePhotoStorage(FileSystemStorage):
    base_url = None

    @property
    def base_location(self):
        return settings.PRIVATE_PHOTO_ROOT

    @property
    def location(self):
        root = Path(self.base_location).resolve()
        if root.is_relative_to(Path(settings.MEDIA_ROOT).resolve()):
            raise ImproperlyConfigured('PRIVATE_PHOTO_ROOT must be outside MEDIA_ROOT.')
        return str(root)


private_storage = PrivatePhotoStorage(file_permissions_mode=0o600, directory_permissions_mode=0o700)


def user_photo_storage():
    return storages['user_photos']


class PrivateBucket(S3Storage):
    def url(self, name, *args, **kwargs):
        raise ValueError('Private photos require an authenticated application route.')


MAX_PHOTO_BYTES = 10 * 1024 * 1024
EXTENSIONS = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}


def normalize_photo(upload):
    """Decode the submitted pixels, apply orientation, and save a fresh metadata-free image."""
    too_large = '사진은 10MB까지 올릴 수 있어요.'
    if upload.size > MAX_PHOTO_BYTES:
        raise ValidationError(too_large)
    upload.seek(0)
    content = upload.read(MAX_PHOTO_BYTES + 1)
    if len(content) > MAX_PHOTO_BYTES:
        raise ValidationError(too_large)
    try:
        with Image.open(BytesIO(content), formats=list(EXTENSIONS)) as image:
            if Image.MAX_IMAGE_PIXELS and image.width * image.height > Image.MAX_IMAGE_PIXELS:
                raise ValidationError('사진의 해상도가 너무 커요.')
            image.verify()
        with Image.open(BytesIO(content), formats=list(EXTENSIONS)) as image:
            kind = image.format
            ImageOps.exif_transpose(image, in_place=True)
            mode = 'RGBA' if image.has_transparency_data else 'RGB'
            with image.convert(mode) as pixels:
                pixels.info.clear()
                output = BytesIO()
                pixels.save(output, format=kind)
                if output.tell() > MAX_PHOTO_BYTES:
                    # Expanding a palette to RGB can make a valid upload larger than the input limit.
                    pixels.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                    output = BytesIO()
                    pixels.save(output, format=kind)
    except (OSError, ValueError, TypeError, SyntaxError, StructError, Image.DecompressionBombError) as exc:
        raise ValidationError('읽을 수 있는 JPEG, PNG 또는 WEBP 사진을 올려 주세요.') from exc
    return ContentFile(output.getvalue(), name=f'{uuid4().hex}.{EXTENSIONS[kind]}')


def private_photo_response(field):
    """Stream a private image after the caller has checked access to its record."""
    try:
        stream = field.open('rb')
    except (ValueError, OSError):
        raise Http404 from None
    response = FileResponse(stream, filename=Path(field.name).name)
    add_never_cache_headers(response)
    response['X-Content-Type-Options'] = 'nosniff'
    return response
