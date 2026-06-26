from django.conf import settings
from django.http import HttpResponse


class UploadSizeLimitMiddleware:
    """
    Rejects POST requests whose Content-Length exceeds MAX_UPLOAD_SIZE
    BEFORE the view is executed. This is more efficient than reading
    the body inside the view because Django's request body hasn't been
    consumed yet — we simply inspect the header.

    Only the /upload/ endpoint (any POST) is relevant, but we apply
    the guard to all POSTs for robustness.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.max_size = getattr(settings, 'MAX_UPLOAD_SIZE', 2 * 1024 * 1024)

    def __call__(self, request):
        if request.method == 'POST':
            content_length = request.META.get('CONTENT_LENGTH', None)
            if content_length is not None:
                try:
                    length = int(content_length)
                except (ValueError, TypeError):
                    length = 0

                if length > self.max_size:
                    max_mb = self.max_size / (1024 * 1024)
                    sent_mb = length / (1024 * 1024)
                    return HttpResponse(
                        f'Upload too large ({sent_mb:.2f} MB). '
                        f'Maximum allowed size is {max_mb:.1f} MB.',
                        status=413,
                        content_type='text/plain',
                    )

        return self.get_response(request)
