from django.urls import path
from . import views

urlpatterns = [
    path('',                    views.builder_home,       name='wb_home'),
    path('new/',                views.builder_new,        name='wb_new'),
    path('<int:build_id>/',     views.builder_detail,     name='wb_detail'),
    path('api/create/',         views.builder_create_api, name='wb_create_api'),
    path('api/<int:build_id>/status/', views.builder_status_api, name='wb_status_api'),
]
