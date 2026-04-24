from django.shortcuts import render


def permission_denied_view(request, exception):
    return render(request, "403.html", status=403)


def page_not_found_view(request, exception):
    return render(request, "404.html", status=404)


def server_error_view(request):
    return render(request, "500.html", status=500)
