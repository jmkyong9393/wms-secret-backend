import datetime
import rsa
import base64
from botocore.signers import CloudFrontSigner
from typing import Dict
from app.core.config import settings
from app.models.wms import now_kst


def rsa_signer(message: bytes) -> bytes:
    """
    Load the private RSA key and sign the message.
    """
    # In a real scenario, this private key should be loaded securely from AWS Secrets Manager or env.
    private_key_content = getattr(settings, "AWS_CLOUDFRONT_PRIVATE_KEY", "").replace(
        "\\n", "\n"
    )
    if not private_key_content:
        # Fallback dummy for local dev/testing
        key = rsa.generate_private_key(2048)
        return rsa.sign(message, key, "SHA-1")
    key = rsa.PrivateKey.load_pkcs1(private_key_content.encode("utf-8"))
    return rsa.sign(message, key, "SHA-1")


def _url_b64encode(data: bytes) -> str:
    return (
        base64.b64encode(data)
        .replace(b"+", b"-")
        .replace(b"=", b"_")
        .replace(b"/", b"~")
        .decode("utf-8")
    )


def generate_signed_cookies(
    resource_url: str, expire_minutes: int = 5
) -> Dict[str, str]:
    """
    Generate CloudFront Signed Cookies for a specific S3 direct upload resource.
    """
    key_id = getattr(settings, "AWS_CLOUDFRONT_KEY_ID", "dummy_key_id")

    # Initialize the signer
    cloudfront_signer = CloudFrontSigner(key_id, rsa_signer)

    # Expiration time
    expire_date = now_kst() + datetime.timedelta(minutes=expire_minutes)

    # Generate the signed policy
    policy = cloudfront_signer.build_policy(
        resource=resource_url, date_less_than=expire_date
    )

    policy_b64 = _url_b64encode(policy.encode("utf-8"))
    signature_bytes = rsa_signer(policy.encode("utf-8"))
    signature_b64 = _url_b64encode(signature_bytes)

    return {
        "CloudFront-Policy": policy_b64,
        "CloudFront-Signature": signature_b64,
        "CloudFront-Key-Pair-Id": key_id,
    }
