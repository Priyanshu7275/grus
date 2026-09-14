/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination:
          "http://grus-api-env.eba-6fgebsix.ap-south-1.elasticbeanstalk.com/:path*",
      },
    ];
  },
};

export default nextConfig;