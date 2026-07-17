"""Blueprint: routes_drive - split from the original monolithic backend_app.py."""


from flask import Blueprint, jsonify, Response, request

import gdrive
from auth import require_auth
from error_manager import safe_route

bp_drive = Blueprint("routes_drive", __name__)



# ---------------------------------------------------------------------
# Google Drive (gdrive.py) - OAuth-authorized by you, see that file's
# docstring for the one-time Google Cloud setup this needs.
# /drive/authorize and /drive/oauth2callback are unprotected on purpose:
# they're navigated to directly (not fetch()'d), so there's no reliable
# way to attach the X-PyBox-Token header to them anyway - same reasoning
# as /admin and /automation/token above.
# ---------------------------------------------------------------------

@bp_drive.route("/drive/authorize")
@safe_route("drive-authorize")
def drive_authorize():
    if not gdrive.has_client_secrets():
        return (
            "client_secrets.json not found at PyBox/client_secrets.json. "
            "See gdrive.py's docstring for the one-time Google Cloud setup "
            "steps.", 400
        )
    from flask import redirect
    return redirect(gdrive.build_authorize_url())


@bp_drive.route("/drive/oauth2callback")
@safe_route("drive-oauth2callback")
def drive_oauth2callback():
    gdrive.handle_callback(request.url)
    return "Google Drive authorized. You can close this and return to PyBox."


@bp_drive.route("/drive/status")
@require_auth
@safe_route("drive-status")
def drive_status():
    creds = gdrive.get_credentials()
    return jsonify({
        "client_secrets_configured": gdrive.has_client_secrets(),
        "authorized": creds is not None,
    })


@bp_drive.route("/drive/files")
@require_auth
@safe_route("drive-files")
def drive_files():
    query = request.args.get("q")
    return jsonify(gdrive.list_files(query=query))


@bp_drive.route("/drive/download/<file_id>")
@require_auth
@safe_route("drive-download")
def drive_download(file_id):
    content, error = gdrive.download_file(file_id)
    if error:
        return jsonify({"error": error}), 400
    return Response(content, mimetype="application/octet-stream")
