from django.urls import path
from . import views

app_name = 'assetreview'

urlpatterns = [
    path('', views.UploadView.as_view(), name='upload'),
    path('review/', views.ReviewView.as_view(), name='review'),
    path('ajax/validate-row/', views.validate_row_ajax, name='validate_row'),
    path('clear/', views.clear_preview, name='clear_preview'),
]
