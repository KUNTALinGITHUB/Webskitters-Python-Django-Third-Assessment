import csv
import io
import json
import os
import uuid
from pathlib import Path

from django.conf import settings
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt

from .forms import CSVUploadForm, REQUIRED_HEADERS, VALID_ASSET_TYPES

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

TEMP_DIR = Path(getattr(settings, 'TEMP_PREVIEW_DIR',
                         Path(settings.BASE_DIR) / 'assetreview' / 'temp_previews'))
MEDIA_DIR = Path(settings.MEDIA_ROOT) / 'asset_manifests'

COOKIE_NAME = 'asset_filter'
VALID_FILTERS = ('all', 'valid', 'warning', 'rejected')

PREVIEW_KEY = 'current_preview'          # single-slot temp file name


def _ensure_dirs():
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)


def _validate_row(row: dict) -> dict:
    """
    Pure row-level validation. Returns the row augmented with
    'status' ('valid' | 'warning' | 'rejected') and 'errors' list.
    Never raises — handles malformed input gracefully.
    """
    errors = []
    warnings = []

    asset_code = str(row.get('asset_code', '')).strip()
    day_no = str(row.get('day_no', '')).strip()
    asset_type = str(row.get('asset_type', '')).strip().lower()
    filename = str(row.get('filename', '')).strip()

    if not asset_code:
        errors.append('asset_code is empty')
    if not filename:
        errors.append('filename is empty')

    if not day_no:
        errors.append('day_no is empty')
    else:
        try:
            val = int(day_no)
            if val < 1:
                errors.append('day_no must be a positive integer')
        except ValueError:
            errors.append(f'day_no "{day_no}" is not an integer')

    if not asset_type:
        errors.append('asset_type is empty')
    elif asset_type not in VALID_ASSET_TYPES:
        warnings.append(
            f'asset_type "{asset_type}" is not in known types '
            f'({", ".join(sorted(VALID_ASSET_TYPES))})'
        )

    if filename and '.' not in filename:
        warnings.append('filename has no file extension')

    if errors:
        status = 'rejected'
    elif warnings:
        status = 'warning'
    else:
        status = 'valid'

    return {**row, 'status': status, 'errors': errors + warnings}


def _parse_csv(file_obj) -> list[dict]:
    """
    Safely parse a CSV file object into a list of validated row dicts.
    Skips completely blank rows without crashing.
    """
    file_obj.seek(0)
    text = file_obj.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for i, raw_row in enumerate(reader, start=2):   # row 1 = headers
        # Normalise keys
        row = {k.strip().lower(): (v.strip() if v else '') for k, v in raw_row.items()
               if k is not None}
        # Skip rows that are entirely blank
        if not any(row.values()):
            continue
        row['row_num'] = i
        rows.append(_validate_row(row))
    return rows


def _save_temp_preview(rows: list[dict]) -> None:
    _ensure_dirs()
    path = TEMP_DIR / f'{PREVIEW_KEY}.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)


def _load_temp_preview() -> list[dict] | None:
    path = TEMP_DIR / f'{PREVIEW_KEY}.json'
    if not path.exists():
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _delete_temp_preview() -> None:
    path = TEMP_DIR / f'{PREVIEW_KEY}.json'
    if path.exists():
        path.unlink(missing_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# Class-Based Views
# ──────────────────────────────────────────────────────────────────────────────

class UploadView(View):
    """
    GET  – renders empty upload form.
    POST – validates form, saves file to MEDIA_ROOT, parses CSV,
           stores preview in temp JSON, redirects to review page.
    """
    template_name = 'assetreview/upload.html'

    def get(self, request):
        form = CSVUploadForm()
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = CSVUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            # Form errors go into context — template just renders {{ form }}
            return render(request, self.template_name, {'form': form})

        uploaded_file = form.cleaned_data['csv_file']

        # Persist file to MEDIA_ROOT/asset_manifests/
        _ensure_dirs()
        safe_name = f'{uuid.uuid4().hex}_{uploaded_file.name}'
        dest = MEDIA_DIR / safe_name
        with open(dest, 'wb') as out:
            for chunk in uploaded_file.chunks():
                out.write(chunk)

        # Re-open saved file to parse (so we stream from disk, not memory)
        with open(dest, 'rb') as saved:
            rows = _parse_csv(saved)

        _save_temp_preview(rows)

        return redirect(reverse('assetreview:review'))


class ReviewView(View):
    """
    GET – loads preview JSON, reads 'asset_filter' cookie, filters rows,
          passes clean context to template.
    """
    template_name = 'assetreview/review.html'

    def get(self, request):
        rows = _load_temp_preview()
        if rows is None:
            # No preview available — redirect back to upload
            return redirect(reverse('assetreview:upload'))

        # Read filter from cookie (default 'all')
        current_filter = request.COOKIES.get(COOKIE_NAME, 'all')
        if current_filter not in VALID_FILTERS:
            current_filter = 'all'

        # Apply filter — business logic in view, not template
        if current_filter == 'all':
            filtered_rows = rows
        else:
            filtered_rows = [r for r in rows if r.get('status') == current_filter]

        # Summary counts
        counts = {s: sum(1 for r in rows if r.get('status') == s)
                  for s in ('valid', 'warning', 'rejected')}
        counts['all'] = len(rows)

        context = {
            'rows': filtered_rows,
            'current_filter': current_filter,
            'counts': counts,
            'valid_filters': VALID_FILTERS,
        }

        response = render(request, self.template_name, context)

        # Persist the chosen filter in a cookie (set regardless — refreshes TTL)
        response.set_cookie(
            COOKIE_NAME,
            current_filter,
            max_age=30 * 24 * 3600,   # 30 days
            httponly=True,
            samesite='Lax',
        )
        return response

    def post(self, request):
        """
        Called when reviewer submits the filter form.
        Reads the new filter, sets cookie, redirects (PRG pattern).
        """
        chosen = request.POST.get('filter', 'all')
        if chosen not in VALID_FILTERS:
            chosen = 'all'

        response = redirect(reverse('assetreview:review'))
        response.set_cookie(
            COOKIE_NAME,
            chosen,
            max_age=30 * 24 * 3600,
            httponly=True,
            samesite='Lax',
        )
        return response


# ──────────────────────────────────────────────────────────────────────────────
# AJAX endpoint
# ──────────────────────────────────────────────────────────────────────────────

@require_POST          # Only POST allowed — GET would be wrong for mutations
def validate_row_ajax(request):
    """
    AJAX POST endpoint. Receives a JSON body with a row dict,
    returns validation status and errors as JSON.

    CSRF is enforced via Django's CsrfViewMiddleware (the JS must
    include the csrfmiddlewaretoken header — see preview.js).
    """
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON body'}, status=400)

    if not isinstance(payload, dict):
        return JsonResponse({'error': 'Payload must be a JSON object'}, status=400)

    # Check required fields present in payload
    missing = [h for h in REQUIRED_HEADERS if h not in payload]
    if missing:
        return JsonResponse(
            {'error': f'Missing fields: {", ".join(missing)}'}, status=400
        )

    result = _validate_row(payload)

    return JsonResponse({
        'status': result['status'],
        'errors': result['errors'],
        'row': {k: v for k, v in result.items() if k not in ('row_num', 'errors', 'status')},
    })


# ──────────────────────────────────────────────────────────────────────────────
# Utility
# ──────────────────────────────────────────────────────────────────────────────

def clear_preview(request):
    """Clears the temp preview file and redirects to upload."""
    _delete_temp_preview()
    return redirect(reverse('assetreview:upload'))
