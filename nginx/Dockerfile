FROM nginx:1.27-alpine

# Install OpenSSL to generate the self-signed certificate during build
RUN apk update && apk add --no-cache openssl

# Create directory to store SSL certificates
RUN mkdir -p /etc/nginx/ssl

# Generate a 2048-bit self-signed certificate valid for 10 years (3650 days)
# The Common Name (CN) is set to a wildcard for convenience (*)
RUN openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/nginx-selfsigned.key \
    -out /etc/nginx/ssl/nginx-selfsigned.crt \
    -subj "/C=BR/ST=State/L=City/O=BackupTeams/OU=API/CN=api.backup-teams.com"

# Remove the default Nginx configuration file
RUN rm /etc/nginx/conf.d/default.conf

# Copy your custom Nginx configuration file
COPY nginx.conf /etc/nginx/conf.d/
