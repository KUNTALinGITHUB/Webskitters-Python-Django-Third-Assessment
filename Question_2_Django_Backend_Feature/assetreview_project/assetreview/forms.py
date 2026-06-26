import csv
import io
from django import forms

REQUIRED_HEADERS = ['asset_code', 'day_no', 'asset_type', 'filename']

VALID_ASSET_TYPES = {'video', 'pdf', 'quiz', 'image', 'audio'}


class CSVUploadForm(forms.Form):
    """
    Form that validates a CSV upload for course asset manifests.
    Enforces required headers and rejects empty files.
    All business validation lives here — never in the template.
    """
    csv_file = forms.FileField(
        label='Asset Manifest CSV',
        help_text='Upload a CSV with columns: asset_code, day_no, asset_type, filename',
        error_messages={'required': 'Please select a CSV file to upload.'},
    )

    def clean_csv_file(self):
        f = self.cleaned_data['csv_file']

        # Reject non-CSV extensions
        if not f.name.lower().endswith('.csv'):
            raise forms.ValidationError('Only .csv files are accepted.')

        # Read raw bytes and decode
        raw = f.read()
        if not raw.strip():
            raise forms.ValidationError('The uploaded file is empty.')

        # Seek back so the view can save the file
        f.seek(0)

        try:
            text = raw.decode('utf-8-sig')  # handle BOM
        except UnicodeDecodeError:
            raise forms.ValidationError('File must be UTF-8 encoded.')

        reader = csv.DictReader(io.StringIO(text))
        headers = reader.fieldnames

        if not headers:
            raise forms.ValidationError('CSV has no header row.')

        # Normalise header names (strip whitespace, lowercase)
        normalised = [h.strip().lower() for h in headers]
        missing = [h for h in REQUIRED_HEADERS if h not in normalised]
        if missing:
            raise forms.ValidationError(
                f'Missing required column(s): {", ".join(missing)}. '
                f'Expected: {", ".join(REQUIRED_HEADERS)}'
            )

        # Confirm at least one data row exists
        rows = list(reader)
        if not rows:
            raise forms.ValidationError('CSV has headers but no data rows.')

        return f
