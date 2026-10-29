"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import HttpResponseNotFound
from django.urls import include, path, re_path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("usuarios.urls")),
    path("", include("core.urls")),
    path("", include("ordenes.urls")),
    path("", include("comunicacion.urls")),
    path("", include("inventario.urls")),
    path("", include("finanzas.urls")),
    path("", include("reportes.urls")),
]

# Los comprobantes de pago son privados: solo se abren por la vista protegida (administrador y cliente dueño)
urlpatterns += [re_path(r"^media/comprobantes/", lambda request, *a, **k: HttpResponseNotFound())]

# Para ver las fotos subidas mientras se trabaja en el computador
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
