import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import { GoogleOAuthProvider } from '@react-oauth/google'
import './globals.css'
import { AuthProvider } from './contexts/AuthContext'

const inter = Inter({ subsets: ['latin'] })

export const metadata: Metadata = {
  title: 'KvalitetTakst - Quality Assessment for Building Reports',
  description: 'Automated quality evaluation of Norwegian building condition reports',
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const googleClientId = process.env.NEXT_PUBLIC_GOOGLE_CLIENT_ID || 'dummy-client-id'

  return (
    <html lang="en">
      <body className={inter.className}>
        <GoogleOAuthProvider clientId={googleClientId}>
          <AuthProvider>
            {children}
          </AuthProvider>
        </GoogleOAuthProvider>
      </body>
    </html>
  )
}

