"""
URL configuration for sql_optimizer project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/queries/', include('queries.urls')),
    path('api/schema/', include('schema_engine.urls')),
]