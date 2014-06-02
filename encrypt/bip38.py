import binascii
import hashlib

import system.address as address
import encrypt.aes as aes
import encrypt.scrypt as scrypt
import num.enc as enc
import os
import random
import struct
import num.elip as elip
import re


def encrypt(privK, address, passphrase, p):
	'''
		BIP0038 private key encryption, Non-EC
	'''
	
	#1. take the first four bytes of SHA256(SHA256()) of it. Let's call this "addresshash".
	addresshash = hashlib.sha256(hashlib.sha256(address).digest()).digest()[:4]  # salt

	#2. Derive a key from the passphrase using scrypt
	#	 a.  Parameters: passphrase is the passphrase itself encoded in UTF-8.
	#		 addresshash came from the earlier step, n=16384, r=8, p=8, length=64
	#		 (n, r, p are provisional and subject to consensus)
	key = scrypt.hash(passphrase, addresshash, 16384, 8, p)
	
	#Let's split the resulting 64 bytes in half, and call them derivedhalf1 and derivedhalf2.
	derivedhalf1 = key[0:32]
	derivedhalf2 = key[32:64]
	
	#3. Do AES256Encrypt(bitcoinprivkey[0...15] xor derivedhalf1[0...15], derivedhalf2), call the 16-byte result encryptedhalf1
	Aes = aes.Aes(derivedhalf2)
	encryptedhalf1 = Aes.enc(enc.sxor(privK[:16], derivedhalf1[:16]))
	
	#4. Do AES256Encrypt(bitcoinprivkey[16...31] xor derivedhalf1[16...31], derivedhalf2), call the 16-byte result encryptedhalf2
	encryptedhalf2 = Aes.enc(enc.sxor(privK[16:32], derivedhalf1[16:32]))
	
	#5. The encrypted private key is the Base58Check-encoded concatenation of the following, which totals 39 bytes without Base58 checksum:
	#		0x01 0x42 + flagbyte + salt + encryptedhalf1 + encryptedhalf2
	flagbyte = chr(0b11100000)  # 11 noec 1 compressedpub 00 future 0 ec only 00 future
	privkey = ('\x01\x42' + flagbyte + addresshash + encryptedhalf1 + encryptedhalf2)
	check = hashlib.sha256(hashlib.sha256(privkey).digest()).digest()[:4]
	return enc.b58encode(privkey + check)
	
def decrypt(encrypted_privkey, passphrase, p):
	
	#1. Collect encrypted private key and passphrase from user.
	#	passed as parameters
	#2. Derive passfactor using scrypt with ownersalt and the user's passphrase and use it to recompute passpoint
	d = enc.b58decode(encrypted_privkey)
	d = d[2:]
	flagbyte = d[0:1]
	d = d[1:]
	addresshash = d[0:4]
	d = d[4:-4]	
	
	#3. Derive decryption key for seedb using scrypt with passpoint, addresshash, and ownersalt
	key = scrypt.hash(passphrase,addresshash, 16384, 8, p)
	derivedhalf1 = key[0:32]
	derivedhalf2 = key[32:64]
	encryptedhalf1 = d[0:16]
	encryptedhalf2 = d[16:32]
	Aes = aes.Aes(derivedhalf2)
	
	#4. Decrypt encryptedpart2 using AES256Decrypt to yield the last 8 bytes of seedb and the last 8 bytes of encryptedpart1.
	decryptedhalf2 = Aes.dec(encryptedhalf2)
	
	#5. Decrypt encryptedpart1 to yield the remainder of seedb.
	decryptedhalf1 = Aes.dec(encryptedhalf1)
	priv = decryptedhalf1 + decryptedhalf2
	priv = binascii.unhexlify('%064x' % (long(binascii.hexlify(priv), 16) ^ long(binascii.hexlify(derivedhalf1), 16)))
	return priv, addresshash

def intermediate(passphrase):
	"""
	Encrypting a private key with EC multiplication offers the ability for someone to generate encrypted keys knowing only an EC point derived from the original passphrase and
	some salt generated by the passphrase's owner, and without knowing the passphrase itself.
	Only the person who knows the original passphrase can decrypt the private key.
	A code known as an intermediate code conveys the information needed to generate such a key without knowledge of the passphrase.

	This methodology does not offer the ability to encrypt a known private key - this means that the process of creating encrypted keys is also the process of generating new addresses.
	On the other hand, this serves a security benefit for someone possessing an address generated this way:
	if the address can be recreated by decrypting its private key with a passphrase, and it's a strong passphrase one can be certain only he knows himself,
	then he can safely conclude that nobody could know the private key to that address.

	The person who knows the passphrase and who is the intended beneficiary of the private keys is called the owner.
	He will generate one or more "intermediate codes", which are the first factor of a two-factor redemption system, and will give them to someone else we'll call printer,
	who generates a key pair with an intermediate code can know the address and encrypted private key, but cannot decrypt the private key without the original passphrase.

	An intermediate code should, but is not required to, embed a printable "lot" and "sequence" number for the benefit of the user.
	The proposal forces these lot and sequence numbers to be included in any valid private keys generated from them.
	An owner who has requested multiple private keys to be generated for him will be advised by applications to ensure that each private key has a unique lot and sequence number
	consistent with the intermediate codes he generated.
	These mainly help protect owner from potential mistakes and/or attacks that could be made by printer.

	The "lot" and "sequence" number are combined into a single 32 bit number.
	20 bits are used for the lot number and 12 bits are used for the sequence number,
	such that the lot number can be any decimal number between 0 and 1048575, and the sequence number can be any decimal number between 0 and 4095.
	For programs that generate batches of intermediate codes for an owner,
	it is recommended that lot numbers be chosen at random within the range 100000-999999 and that sequence numbers are assigned starting with 1.

	Steps performed by owner to generate a single intermediate code, if lot and sequence numbers are being included:

	"""

	#1. Generate 4 random bytes, call them ownersalt.
	ownersalt = os.urandom(4)

	#2. Encode the lot and sequence numbers as a 4 byte quantity (big-endian): lotnumber * 4096 + sequencenumber. Call these four bytes lotsequence.
	lotnumber = random.randint(0,1048575)
	sequencenumber = random.randint(0,4095)
	lotsequence = struct.pack('>I', (lotnumber * 4096 + sequencenumber))

	#3. Concatenate ownersalt + lotsequence and call this ownerentropy.
	ownerentropy = ownersalt + lotsequence

	#4. Derive a key from the passphrase using scrypt
	#Parameters: passphrase is the passphrase itself encoded in UTF-8. salt is ownersalt. n=16384, r=8, p=8, length=32.
	#Call the resulting 32 bytes prefactor.
	prefactor = scrypt.hash(passphrase, ownersalt, 16384, 8, 8, 32)

	#5. Take SHA256(SHA256(prefactor + ownerentropy)) and call this passfactor.
	passfactor = hashlib.sha256(hashlib.sha256(prefactor + ownerentropy).digest()).digest()

	#6. Compute the elliptic curve point G * passfactor, and convert the result to compressed notation (33 bytes). Call this passpoint.
	#Compressed notation is used for this purpose regardless of whether the intent is to create Bitcoin addresses with or without compressed public keys.
	pub = elip.base10_multiply(elip.G, enc.decode(passfactor, 256))
	passpoint = ('0' + str(2 + (pub[1] % 2)) + enc.encode(pub[0], 16, 64)).decode('hex')

	#7. Convey ownersalt and passpoint to the party generating the keys, along with a checksum to ensure integrity.
	#The following Base58Check-encoded format is recommended for this purpose: magic bytes "2C E9 B3 E1 FF 39 E2 51" followed by ownerentropy, and then passpoint.
	#The resulting string will start with the word "passphrase" due to the constant bytes,
	#will be 72 characters in length, and encodes 49 bytes (8 bytes constant + 8 bytes ownerentropy + 33 bytes passpoint).
	#The checksum is handled in the Base58Check encoding. The resulting string is called intermediate_passphrase_string.
	inp_fmtd = '\x2C\xE9\xB3\xE1\xFF\x39\xE2\x51' + ownerentropy + passpoint
	leadingzbytes = len(re.match('^\x00*',inp_fmtd).group(0))
	hash =  hashlib.sha256(hashlib.sha256(inp_fmtd).digest()).digest().encode('hex')
	checksum = hash[:4]
	intermermediate_passphrase_string = '1' * leadingzbytes + enc.encode(enc.decode(inp_fmtd+checksum,256),58)
	return intermermediate_passphrase_string

def intermediate2privK(intermediate_passphrase_string):
	"""
	Steps to create new encrypted private keys given intermediate_passphrase_string from owner
	(so we have ownerentropy, and passpoint, but we do not have passfactor or the passphrase):
	"""

	#get ownerentropy and passpoint from the intermediate key
	leadingzbytes = len(re.match('^1*',intermediate_passphrase_string).group(0))
	data = '\x00' * leadingzbytes + enc.encode(enc.decode(intermediate_passphrase_string,58),256)
	assert hashlib.sha256(hashlib.sha256(data[:-4]).digest()).digest().encode('hex')[:4] == data[-4:]
	decodedstring = data[1:-4]
	ownerentropy = decodedstring[7:15]
	passpoint = decodedstring[-33:]

	#1. Set flagbyte.
	#Turn on bit 0x20 if the Bitcoin address will be formed by hashing the compressed public key (optional, saves space, but many Bitcoin implementations aren't compatible with it)
	#Turn on bit 0x04 if ownerentropy contains a value for lotsequence.
	#(While it has no effect on the keypair generation process, the decryption process needs this flag to know how to process ownerentropy)
	flagbyte = chr(0b00100100) # 00 EC 1 compressed 00 future 1 has lot and sequence 00 future
	#flagbyte = chr(0b11100000)  # 11 noec 1 compressedpub 00 future 0 ec only 00 future

	#2. Generate 24 random bytes, call this seedb. Take SHA256(SHA256(seedb)) to yield 32 bytes, call this factorb.
	seedb = os.urandom(24)
	factorb = hashlib.sha256(hashlib.sha256(seedb).digest()).digest()

	#3. ECMultiply passpoint by factorb.
	pub = elip.base10_multiply(int(enc.decode(factorb, 256)), int(enc.decode(passpoint, 256)))

	#4. Use the resulting EC point as a public key and hash it into a Bitcoin address using either compressed or uncompressed public key methodology
	# (specify which methodology is used inside flagbyte).
	# This is the generated Bitcoin address, call it generatedaddress.
	publicKey = ('0' + str(2 + (pub[1] % 2)) + enc.encode(pub[0], 16, 64)).decode('hex')
	generatedaddress = address.publicKey2Address(publicKey.encode('hex')) ## Remember to add in the currency details here

	#5. Take the first four bytes of SHA256(SHA256(generatedaddress)) and call it addresshash.
	addresshash = hashlib.sha256(hashlib.sha256(generatedaddress).digest()).digest()

	#6. Now we will encrypt seedb. Derive a second key from passpoint using scrypt
	#Parameters: passphrase is passpoint provided from the first party (expressed in binary as 33 bytes).
	# salt is addresshash + ownerentropy, n=1024, r=1, p=1, length=64. The "+" operator is concatenation.
	encseedb = scrypt.hash(passpoint, addresshash + ownerentropy, 1024, 1, 1, 64)

	#7. Split the result into two 32-byte halves and call them derivedhalf1 and derivedhalf2.
	derivedhalf1 = encseedb[0:32]
	derivedhalf2 = encseedb[32:64]

	#8. Do AES256Encrypt(seedb[0...15] xor derivedhalf1[0...15], derivedhalf2), call the 16-byte result encryptedpart1
	Aes = aes.Aes(derivedhalf2)
	encryptedpart1 = Aes.enc(enc.sxor(seedb[:16], derivedhalf1[:16]))

	#9. Do AES256Encrypt((encryptedpart1[8...15] + seedb[16...23]) xor derivedhalf1[16...31], derivedhalf2), call the 16-byte result encryptedpart2.
	# The "+" operator is concatenation.
	encryptedpart2 = Aes.enc(enc.sxor(encryptedpart1[8:16] + seedb[16:24], derivedhalf1[16:32]))

	#10. The encrypted private key is the Base58Check-encoded concatenation of the following, which totals 39 bytes without Base58 checksum:
	#0x01 0x43 + flagbyte + addresshash + ownerentropy + encryptedpart1[0...7] + encryptedpart2
	inp_fmtd = '\x01\x43' + flagbyte + addresshash + ownerentropy + encryptedpart1[0:8] + encryptedpart2
	check = hashlib.sha256(hashlib.sha256(inp_fmtd).digest()).digest()[:4]
	print(len(inp_fmtd))
	BIPKey = enc.b58encode(inp_fmtd + check)
	return BIPKey, generatedaddress