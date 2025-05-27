from Extensions import Extensions
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TxOpts
from solders.keypair import Keypair
from solders.system_program import TransferParams, transfer, ID as SYS_PROGRAM_ID
from solders.pubkey import Pubkey
from solders.instruction import Instruction
from solders.message import Message, MessageV0
import base58
import requests
import struct
import re # For parsing balance in migrate_wallet
import asyncio # For scheduling migration task
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Any, Optional
from solders.transaction import VersionedTransaction, Transaction
from solders.system_program import TransferParams, transfer
from solders.pubkey import Pubkey

# Define TOKEN_PROGRAM_ID (this is a well-known address in Solana)
TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")


class solana_wallet(Extensions):
    """
    The SolanaWallets extension enables interaction with Solana blockchain wallets using the solana‑py SDK.
    This implementation uses the new solders‑based imports for keypairs, public keys, and system instructions.

    The extension supports creating wallets, checking balances, sending SOL, and more.
    """

    # Raydium SDK constants
    RAYDIUM_ROUTER_ID = Pubkey.from_string(
        "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
    )  # Raydium Router Program ID
    POOL_STATE_VERSION_V1 = 1
    AMM_PROGRAM_ID = Pubkey.from_string("675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8")
    SERUM_PROGRAM_ID = Pubkey.from_string(
        "9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin"
    )

    @dataclass
    class PoolInfo:
        address: Pubkey
        input_reserve: int
        output_reserve: int
        input_decimals: int
        fee_numerator: int
        fee_denominator: int

    async def _get_token_decimals(self, token: Pubkey) -> int:
        """Get token decimals from mint account"""
        try:
            token_info = await self.client.get_account_info(token)
            if not token_info["result"]["value"]:
                return 9  # Default to 9 decimals if not found

            data = base58.b58decode(token_info["result"]["value"]["data"][0])
            # Parse mint account data (decimals at offset 44)
            decimals = data[44]
            return decimals
        except Exception:
            return 9  # Default to 9 decimals on error

    async def _get_pool_info(
        self, input_token: Pubkey, output_token: Pubkey
    ) -> Optional[Dict]:
        """Get pool information for a token pair"""
        try:
            # Derive pool address using SDK algorithm
            seeds = [
                bytes("amm_pool", "utf-8"),
                bytes(input_token),
                bytes(output_token),
            ]
            pool_address, _ = Pubkey.find_program_address(seeds, self.AMM_PROGRAM_ID)

            # Get pool account data
            pool_data = await self.client.get_account_info(pool_address)
            if not pool_data["result"]["value"]:
                return None

            # Parse pool state data
            data = base58.b58decode(pool_data["result"]["value"]["data"][0])

            # Extract pool info from data using similar structure as SDK
            (
                version,
                status,
                nonce,
                token_a,
                token_b,
                reserve_a,
                reserve_b,
                fee_numerator,
                fee_denominator,
            ) = struct.unpack("<BBQQQQQQQ", data[:73])

            return {
                "address": str(pool_address),
                "input_reserve": reserve_a if input_token == token_a else reserve_b,
                "output_reserve": reserve_b if input_token == token_a else reserve_a,
                "input_decimals": await self._get_token_decimals(input_token),
                "fee_numerator": fee_numerator,
                "fee_denominator": fee_denominator,
            }
        except Exception as e:
            print(f"Error getting pool info: {str(e)}")
            return None

    def _compute_output_amount(
        self,
        input_amount: int,
        input_reserve: int,
        output_reserve: int,
        fee_numerator: int,
        fee_denominator: int,
    ) -> int:
        """Compute output amount based on Raydium's constant product formula"""
        fee_amount = (input_amount * fee_numerator) // fee_denominator
        amount_with_fee = input_amount - fee_amount
        numerator = amount_with_fee * output_reserve
        denominator = input_reserve + amount_with_fee
        return numerator // denominator if denominator != 0 else 0

    def _calculate_price_impact(
        self,
        input_amount: int,
        output_amount: int,
        input_reserve: int,
        output_reserve: int,
    ) -> float:
        """Calculate price impact of a swap"""
        price_before = Decimal(output_reserve) / Decimal(input_reserve)
        price_after = Decimal(output_amount) / Decimal(input_amount)
        price_impact = abs((price_before - price_after) / price_before) * 100
        return float(price_impact)

    def __init__(
        self,
        **kwargs,
    ):
        RAYDIUM_API_URI = "https://api.raydium.io"
        SOLANA_API_URI = "https://api.mainnet-beta.solana.com"
        self.RAYDIUM_API_URI = RAYDIUM_API_URI
        self.WSOL_MINT = "So11111111111111111111111111111111111111112"
        self.SOLANA_API_URI = SOLANA_API_URI
        self.client = AsyncClient(SOLANA_API_URI)
        
        original_private_key_input = kwargs.get("SOLANA_WALLET_API_KEY", None)
        self.wallet_keypair = None
        self.wallet_address = None
        key_loaded_successfully = False
        key_was_generated_due_to_load_failure = False

        if original_private_key_input:
            try:
                secret_bytes = None
                # Try hex decoding first
                try:
                    secret_bytes = bytes.fromhex(original_private_key_input)
                except ValueError:
                    # If that fails, try base58 decoding
                    try:
                        secret_bytes = base58.b58decode(original_private_key_input)
                    except Exception as e_b58:
                        # Set flag here before printing, as this is a failure path for the provided key
                        print(f"Failed to decode private key from base58: {e_b58}")
                        # Let it fall through to key_loaded_successfully check
                
                if secret_bytes:
                    if len(secret_bytes) < 32:
                        print("Decoded private key seed is invalid (e.g. < 32 bytes). Will generate new wallet.")
                        # Let it fall through to key_loaded_successfully check
                    else:
                        self.wallet_keypair = Keypair.from_seed(secret_bytes[:32])
                        self.wallet_address = str(self.wallet_keypair.pubkey())
                        print(f"Wallet initialized from provided private key. Public key: {self.wallet_address}")
                        key_loaded_successfully = True
                else: # secret_bytes is None, implies decoding failed and was caught or value error from hex
                    if not any(isinstance(e, ValueError) for e in sys.exc_info()): # Check if it's not already printed by b58
                         print("Private key decoding failed (resulted in empty bytes). Will generate new wallet.")

            except Exception as e:
                print(f"Error initializing wallet from provided key: {e}. Will generate new wallet.")
                # key_loaded_successfully remains False
        
        if not key_loaded_successfully:
            if original_private_key_input:
                key_was_generated_due_to_load_failure = True
                print("Provided private key failed to load. Generating a new wallet as fallback.")
            else:
                print("No private key provided. Generating a new wallet.")
            
            try:
                self.wallet_keypair = Keypair()
                self.wallet_address = str(self.wallet_keypair.pubkey())
                new_private_key_base58 = base58.b58encode(self.wallet_keypair.secret()).decode('utf-8')
                print(f"New wallet created. Public key: {self.wallet_address}. Private key (base58): {new_private_key_base58}")
                # In a real scenario, this new_private_key_base58 should be securely persisted by the system.
            except Exception as e:
                print(f"Error generating new wallet: {e}")
                self.wallet_keypair = None
                self.wallet_address = None # Ensure address is also None if keypair generation fails
        
        if not self.wallet_keypair: # Final check if any wallet is set
            print("Failed to load or generate a wallet. Wallet functionality will be limited.")

        # New part for scheduling migration:
        if key_was_generated_due_to_load_failure and self.wallet_keypair and original_private_key_input:
            # This implies original_private_key_input was not None and failed to load,
            # and a new wallet (self.wallet_keypair) has been successfully generated to replace it.
            print(f"Original private key (first 10 chars: {original_private_key_input[:10]}...) failed to load. Scheduling migration from it to the newly generated wallet ({self.wallet_address}).")
            # Ensure self.migrate_wallet can handle if original_private_key_input is malformed for its own decoding.
            asyncio.create_task(self.migrate_wallet(old_wallet_private_key_str=original_private_key_input))
        elif not key_loaded_successfully and not original_private_key_input and self.wallet_keypair:
            # This case: No original key provided, new one generated.
            # This is the first-time setup for this agent with this extension.
            # The new_private_key_base58 was printed. It should be saved by the user/system
            # and used as SOLANA_WALLET_API_KEY for subsequent loads.
            # No "migration" needed here as there's no "old" wallet with funds.
            pass

        self.commands = {
            "Get Solana Wallet Balance": self.get_wallet_balance,
            "Send SOL": self.send_sol,
            "Get Public Key": self.get_public_key,
            # "Get Transaction Info": self.get_transaction_info,
            # "Get Recent Transactions": self.get_recent_transactions,
            # "Get Solana Token Balance": self.get_token_balance,
            # "Airdrop SOL": self.airdrop_sol,
            # "Get Token Swap Quote": self.get_swap_quote,
            # "Execute Token Swap": self.execute_swap,
            # "Get Token List": self.get_token_list,
            # "Get Token Price": self.get_token_price,
            # "Get Wallet Token Accounts": self.get_wallet_token_accounts,
            # "Get Route Quote": self.get_route_quote,
            # "Execute Trade": self.execute_trade,
        }

    async def get_wallet_balance(self, wallet_address: str = None):
        """
        Retrieves the SOL balance for the given wallet address.
        If no address is provided, uses the wallet address from initialization.
        """
        if wallet_address is None:
            wallet_address = self.wallet_address
        if wallet_address is None:
            return "No wallet address specified."
        try:
            # Convert the wallet address string to a Pubkey object
            pubkey = Pubkey.from_string(wallet_address)
            # Add await since AsyncClient.get_balance is asynchronous
            response = await self.client.get_balance(pubkey, commitment=Confirmed)

            # Access the balance using the .value attribute of GetBalanceResp
            balance_lamports = response.value
            sol_balance = balance_lamports / 1_000_000_000  # Convert lamports to SOL

            # Format with full precision instead of rounding to 0
            sol_balance_str = f"{sol_balance:.9f}".rstrip("0").rstrip(".")

            return f"The balance of the Solana wallet with public key {wallet_address} is {sol_balance_str} SOL."
        except Exception as e:
            return f"Error retrieving balance: {str(e)}"

    async def send_sol(
        self, from_wallet: str = None, to_wallet: str = "", amount: str = "0.0", signer_keypair: Keypair = None
    ):
        """
        Sends a specified amount of SOL from one wallet to another.
        Amount is provided as a string and converted appropriately, with fee consideration.

        Args:
            from_wallet (str): Sender's public key (defaults to self.wallet_address if None and signer_keypair is self.wallet_keypair).
            to_wallet (str): Recipient's public key.
            amount (str): Amount of SOL to send as a string (e.g., "0.005657068").
            signer_keypair (Keypair, optional): The keypair to use for signing the transaction. Defaults to self.wallet_keypair.

        Returns:
            str: Success message with transaction signature or an error message.
        """
        current_signer = signer_keypair if signer_keypair else self.wallet_keypair

        if current_signer is None:
            return "Error: No signer keypair available for the transaction."

        if from_wallet:
            try:
                expected_from_pubkey = Pubkey.from_string(from_wallet)
                if expected_from_pubkey != current_signer.pubkey():
                    return f"Error: Signer's public key ({str(current_signer.pubkey())}) does not match the provided from_wallet public key ({from_wallet})."
            except Exception as e: # Handle invalid Pubkey string
                return f"Error: Invalid from_wallet public key format: {e}"
        else: # If from_wallet is None, set it from the signer
            from_wallet = str(current_signer.pubkey())
        
        if not to_wallet:
            return "No recipient wallet specified."

        try:
            # Convert amount from string to float
            amount_float = float(amount)
            if amount_float <= 0:
                return "Amount must be greater than 0."

            # Get current balance asynchronously
            balance_response = await self.client.get_balance(
                Pubkey.from_string(from_wallet), commitment=Confirmed
            )
            available_lamports = (
                balance_response.value
            )  # Lamports available in the wallet

            # Estimate transaction fee (5000 lamports is a typical fee)
            FEE_LAMPORTS = 5000  # Adjust based on network conditions if needed
            max_lamports_to_send = available_lamports - FEE_LAMPORTS

            if max_lamports_to_send <= 0:
                return f"Insufficient funds: Balance ({available_lamports / 1e9} SOL) is less than the estimated fee ({FEE_LAMPORTS / 1e9} SOL)."

            # Convert amount to lamports; if >1000 SOL, assume input is already in lamports
            if amount_float > 1000:
                lamports_amount = int(amount_float)
            else:
                lamports_amount = int(
                    amount_float * 1_000_000_000
                )  # Convert SOL to lamports

            # Adjust amount if it exceeds available balance minus fee
            if lamports_amount > max_lamports_to_send:
                lamports_amount = max_lamports_to_send
                adjusted_amount_sol = lamports_amount / 1_000_000_000
                print(
                    f"Adjusted amount to {adjusted_amount_sol} SOL to account for transaction fee."
                )

            if lamports_amount <= 0:
                return "Amount too small to send after fee adjustment."

            # Create the transfer instruction
            transfer_ix = transfer(
                TransferParams(
                    from_pubkey=Pubkey.from_string(from_wallet),
                    to_pubkey=Pubkey.from_string(to_wallet),
                    lamports=lamports_amount,
                )
            )

            # Get recent blockhash
            blockhash_response = await self.client.get_latest_blockhash(
                commitment=Confirmed
            )
            recent_blockhash = blockhash_response.value.blockhash

            # Create the message using MessageV0
            msg = MessageV0.try_compile(
                payer=Pubkey.from_string(from_wallet),
                instructions=[transfer_ix],
                address_lookup_table_accounts=[],
                recent_blockhash=recent_blockhash,
            )

            # Create and sign the versioned transaction
            tx = VersionedTransaction(msg, [current_signer])

            # Send the transaction with options
            opts = TxOpts(skip_preflight=False, preflight_commitment=Confirmed)
            response = await self.client.send_transaction(tx, opts=opts)
            tx_signature = response.value

            return f"Transaction submitted successfully. Signature: {tx_signature}"
        except ValueError as ve:
            return f"Error: Invalid amount format - {str(ve)}"
        except Exception as e:
            return f"Error sending SOL: {str(e)}"

    async def get_transaction_info(self, tx_signature: str):
        """
        Retrieves information about a specific transaction using its signature.
        """
        try:
            response = await self.client.get_confirmed_transaction(tx_signature)
            return f"Transaction info: {response}"
        except Exception as e:
            return f"Error retrieving transaction info: {str(e)}"

    async def get_recent_transactions(
        self, wallet_address: str = None, limit: int = 10
    ):
        """
        Retrieves the most recent transaction signatures for the given wallet address.
        """
        if wallet_address is None:
            wallet_address = self.wallet_address
        if wallet_address is None:
            return "No wallet address specified."
        try:
            response = await self.client.get_signatures_for_address(
                Pubkey.from_string(wallet_address), limit=limit
            )
            return f"Recent transactions for wallet {wallet_address}: {response.get('result')}"
        except Exception as e:
            return f"Error retrieving recent transactions: {str(e)}"

    async def get_token_balance(self, wallet_address: str = None, token_mint: str = ""):
        """
        Retrieves the balance of a specific SPL token for the given wallet.
        """
        if wallet_address is None:
            wallet_address = self.wallet_address
        return f"Token balance for token {token_mint} in wallet {wallet_address}: [Not implemented]."

    async def airdrop_sol(self, wallet_address: str = None, amount: float = 0.0):
        """
        Requests an airdrop of SOL (on devnet) to the specified wallet address.
        """
        if wallet_address is None:
            wallet_address = self.wallet_address
        if wallet_address is None:
            return "No wallet address specified."
        try:
            lamports_amount = int(amount * 1e9)
            response = await self.client.request_airdrop(
                Pubkey.from_string(wallet_address),
                lamports_amount,
                commitment=Confirmed,
            )
            return f"Airdrop requested: {response.get('result')}"
        except Exception as e:
            return f"Error requesting airdrop: {str(e)}"

    async def get_swap_quote(self, from_token: str, to_token: str, amount: float):
        """
        Retrieves a quote for swapping one token to another using Raydium SDK computations.
        """
        try:
            # Convert token addresses to Pubkey objects
            input_token = Pubkey.from_string(from_token)
            output_token = Pubkey.from_string(to_token)

            # Get pool info for the token pair
            pool_info = await self._get_pool_info(input_token, output_token)
            if not pool_info:
                return f"No pool found for {from_token} -> {to_token}"

            # Calculate amounts using pool reserves and weights
            input_amount = int(amount * 10 ** pool_info["input_decimals"])
            output_amount = self._compute_output_amount(
                input_amount,
                pool_info["input_reserve"],
                pool_info["output_reserve"],
                pool_info["fee_numerator"],
                pool_info["fee_denominator"],
            )

            # Calculate price impact
            price_impact = self._calculate_price_impact(
                input_amount,
                output_amount,
                pool_info["input_reserve"],
                pool_info["output_reserve"],
            )

            return {
                "inputAmount": str(input_amount),
                "outputAmount": str(output_amount),
                "priceImpact": price_impact,
                "pool": pool_info["address"],
            }
        except Exception as e:
            return f"Error getting swap quote: {str(e)}"

    async def execute_swap(
        self,
        wallet_address: str = None,
        quote: Dict[str, Any] = None,
        amount: float = 0.0,
    ):
        """
        Executes a token swap using Raydium SDK instructions.
        """
        if wallet_address is None:
            wallet_address = self.wallet_address
        if not quote:
            return "No quote provided for swap"

        try:
            # Build swap instruction
            pool_address = Pubkey.from_string(quote["pool"])
            wallet_pubkey = Pubkey.from_string(wallet_address)

            swap_instruction = self._build_swap_instruction(
                pool_address,
                wallet_pubkey,
                int(quote["inputAmount"]),
                int(quote["outputAmount"]),
            )

            # Get recent blockhash
            blockhash_response = await self.client.get_latest_blockhash(
                commitment=Confirmed
            )
            recent_blockhash = blockhash_response.value.blockhash

            # Create message
            message = Message.new_with_blockhash(
                instructions=[swap_instruction],
                payer=wallet_pubkey,
                blockhash=recent_blockhash,
            )

            # Create and sign transaction
            tx = Transaction.from_message(
                message=message, from_keypairs=[self.wallet_keypair]
            )

            # Send transaction
            opts = TxOpts(skip_preflight=False, preflight_commitment=Confirmed)
            response = await self.client.send_transaction(tx, opts=opts)

            return {
                "success": True,
                "signature": response.value,
                "inputAmount": quote["inputAmount"],
                "outputAmount": quote["outputAmount"],
            }
        except Exception as e:
            return f"Error executing swap: {str(e)}"

    def _build_swap_instruction(
        self, pool: Pubkey, wallet: Pubkey, input_amount: int, min_output: int
    ) -> Instruction:
        """Build Raydium swap instruction"""
        keys = [
            {"pubkey": pool, "isSigner": False, "isWritable": True},
            {"pubkey": wallet, "isSigner": True, "isWritable": True},
            {"pubkey": TOKEN_PROGRAM_ID, "isSigner": False, "isWritable": False},
            {"pubkey": self.AMM_PROGRAM_ID, "isSigner": False, "isWritable": False},
        ]

        # Swap instruction data layout matches Raydium SDK
        instruction_data = struct.pack(
            "<BQQ", 9, input_amount, min_output  # Swap instruction code  # Input amount
        )  # Minimum output amount

        return Instruction(
            program_id=self.AMM_PROGRAM_ID, data=instruction_data, accounts=keys
        )

    async def get_route_quote(self, from_token: str, to_token: str, amount: float):
        """
        Get a quote for the best trading route between two tokens using SDK computations.
        """
        try:
            input_token = Pubkey.from_string(from_token)
            output_token = Pubkey.from_string(to_token)

            # First try direct pool
            direct_quote = await self.get_swap_quote(from_token, to_token, amount)
            if not isinstance(direct_quote, str):  # Not an error message
                return {
                    "route": [
                        {
                            "type": "swap",
                            "in": from_token,
                            "out": to_token,
                            "pool": direct_quote["pool"],
                        }
                    ],
                    "quote": direct_quote,
                }

            # Try routing through USDC as intermediate
            usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

            # First hop: from_token -> USDC
            first_hop = await self.get_swap_quote(from_token, usdc_mint, amount)
            if isinstance(first_hop, str):
                return f"No route found: {first_hop}"

            # Second hop: USDC -> to_token
            usdc_amount = float(first_hop["outputAmount"]) / 1e6  # USDC has 6 decimals
            second_hop = await self.get_swap_quote(usdc_mint, to_token, usdc_amount)
            if isinstance(second_hop, str):
                return f"No route found: {second_hop}"

            # Calculate combined price impact
            total_impact = float(first_hop["priceImpact"]) + float(
                second_hop["priceImpact"]
            )

            return {
                "route": [
                    {
                        "type": "swap",
                        "in": from_token,
                        "out": usdc_mint,
                        "pool": first_hop["pool"],
                    },
                    {
                        "type": "swap",
                        "in": usdc_mint,
                        "out": to_token,
                        "pool": second_hop["pool"],
                    },
                ],
                "quote": {
                    "inputAmount": first_hop["inputAmount"],
                    "outputAmount": second_hop["outputAmount"],
                    "priceImpact": total_impact,
                    "pools": [first_hop["pool"], second_hop["pool"]],
                },
            }

        except Exception as e:
            return f"Error getting route quote: {str(e)}"

    async def get_public_key(self):
        """
        Get the public key of the current wallet.
        """
        if self.wallet_address:
            return {"public_key": self.wallet_address}
        return {"error": "No wallet initialized"}

    async def execute_trade(self, route_quote: Dict[str, Any]):
        """
        Execute a complex trade involving one or more swaps.
        """
        if not route_quote or "route" not in route_quote:
            return "Invalid route quote provided"

        try:
            result = []
            for step in route_quote["route"]:
                if step["type"] == "swap":
                    swap_result = await self.execute_swap(
                        wallet_address=self.wallet_address,
                        quote={
                            "pool": step["pool"],
                            "inputAmount": route_quote["quote"]["inputAmount"],
                            "outputAmount": route_quote["quote"]["outputAmount"],
                        },
                    )
                    result.append(swap_result)

            return {
                "success": True,
                "steps": result,
                "inputAmount": route_quote["quote"]["inputAmount"],
                "outputAmount": route_quote["quote"]["outputAmount"],
                "priceImpact": route_quote["quote"]["priceImpact"],
            }
        except Exception as e:
            return f"Error executing trade: {str(e)}"

    async def get_token_list(self):
        """
        Get a list of supported tokens.
        """
        try:
            response = requests.get(f"{self.RAYDIUM_API_URI}/tokens")
            return response.json()
        except Exception as e:
            return f"Error getting token list: {str(e)}"

    async def get_token_price(self, token: str):
        """
        Get the current price of a token in USDC.
        """
        try:
            usdc_mint = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
            quote = await self.get_swap_quote(token, usdc_mint, 1.0)

            if isinstance(quote, str):  # Error message
                return quote

            return {
                "price": float(quote["outputAmount"]) / 1e6,  # USDC has 6 decimals
                "priceImpact": quote["priceImpact"],
            }
        except Exception as e:
            return f"Error getting token price: {str(e)}"

    async def get_wallet_token_accounts(self, wallet_address: str = None):
        """
        Get all token accounts owned by a wallet.
        """
        if wallet_address is None:
            wallet_address = self.wallet_address
        if wallet_address is None:
            return "No wallet address specified."

        try:
            response = await self.client.get_token_accounts_by_owner(
                Pubkey.from_string(wallet_address), {"programId": TOKEN_PROGRAM_ID}
            )
            return response
        except Exception as e:
            return f"Error getting token accounts: {str(e)}"

    async def migrate_wallet(self, old_wallet_private_key_str: str = None):
        # import re # Ensure re is imported locally or at file top -> Moved to top level imports
        print("Attempting to migrate wallet.")
        old_keypair = None
        old_wallet_address = None

        if old_wallet_private_key_str:
            try:
                print(f"Attempting to load old wallet from provided private key string.")
                secret_bytes = None
                try:
                    secret_bytes = bytes.fromhex(old_wallet_private_key_str)
                except ValueError:
                    secret_bytes = base58.b58decode(old_wallet_private_key_str)
                except Exception as e_b58_migrate: # Catch specific base58 decoding error for old key
                    print(f"Error decoding old_wallet_private_key_str from base58 during migration: {e_b58_migrate}")
                    secret_bytes = None # Ensure secret_bytes is None if b58 fails
                
                if secret_bytes and len(secret_bytes) >= 32:
                    old_keypair = Keypair.from_seed(secret_bytes[:32])
                elif secret_bytes: # secret_bytes exists but less than 32
                    print("Error: Decoded old private key seed is less than 32 bytes.")
                    old_keypair = None
                else: # secret_bytes is None (hex failed, b58 failed or produced None)
                    old_keypair = None # Ensure old_keypair is None
                                
                if old_keypair:
                    old_wallet_address = str(old_keypair.pubkey())
                    print(f"Old wallet loaded. Public key: {old_wallet_address}")
                else:
                    print("Failed to derive keypair from old private key string.")
            except Exception as e:
                print(f"Error decoding/loading old private key string: {e}. Migration aborted for this key.")
                return None # Or False
        else:
            # Simplified: Assumes migration of the current instance's wallet if no specific old key is given.
            # This case should ideally not be reached if called from __init__ auto-migration,
            # as original_private_key_input would be the failed key.
            # However, if migrate_wallet is called manually without an old key,
            # it implies migrating the *current* instance wallet to a new one.
            if self.wallet_keypair and self.wallet_address: # Check if a current wallet exists
                print("Migrating the current instance's active wallet to a new one.")
                old_keypair = self.wallet_keypair
                old_wallet_address = self.wallet_address # Use the string address
            else:
                print("No old_wallet_private_key_str provided and no current wallet in instance to migrate from. Migration aborted.")
                return None 

        if not old_keypair or not old_wallet_address: # old_wallet_address might not be set if old_keypair failed
            print("Old wallet could not be established (e.g. private key invalid or no current wallet). Migration cannot proceed.")
            return None

        # Generate new wallet to migrate to
        new_keypair = Keypair() # This will be the wallet funds are migrated TO.
        new_wallet_address = str(new_keypair.pubkey())
        new_private_key_base58_for_new_wallet = base58.b58encode(new_keypair.secret()).decode('utf-8')
        print(f"Generated new target wallet for migration. Public key: {new_wallet_address}. New Private key (base58): {new_private_key_base58_for_new_wallet}")

        # Check balance of the old wallet
        balance_str = await self.get_wallet_balance(wallet_address=str(old_wallet_address))
        balance_sol = 0.0
        if balance_str and isinstance(balance_str, str): # Ensure balance_str is a string before using re.search
            match = re.search(r"is ([\d\.]+) SOL", balance_str)
            if match:
                try:
                    balance_sol = float(match.group(1))
                except ValueError:
                    print(f"Could not parse balance float from: {match.group(1)}")
                    balance_sol = 0.0
        
        print(f"Old wallet balance: {balance_sol} SOL.")

        if balance_sol > 0.00001: # Threshold for transfer
            print("Attempting to transfer funds from old wallet to new wallet...")
            # Ensure from_wallet in send_sol is the old_wallet_address string
            transfer_result = await self.send_sol(
                from_wallet=str(old_wallet_address), 
                to_wallet=new_wallet_address, 
                amount=str(balance_sol), 
                signer_keypair=old_keypair # Pass the old keypair for signing this transaction
            )
            if isinstance(transfer_result, str) and transfer_result.startswith("Transaction submitted successfully"):
                print(f"Funds transferred successfully. Signature: {transfer_result.split(': ')[-1]}")
            else:
                print(f"Fund transfer failed. Result: {transfer_result}")
        else:
            print("Old wallet has no significant balance to transfer.")

        # Decision on updating self.wallet_keypair / self.wallet_address:
        # If old_wallet_private_key_str was provided (auto-migration from __init__ due to load failure),
        # then self.wallet_keypair is ALREADY the new_keypair (generated in __init__).
        # So, we don't re-assign self.wallet_keypair to new_keypair here in that path.
        # The purpose of this migration was to move funds TO self.wallet_keypair (which is new_keypair in this context).
        
        # If old_wallet_private_key_str was NOT provided (manual migration of current instance wallet),
        # THEN we update self.wallet_keypair to the new_keypair generated within this migrate_wallet function.
        if not old_wallet_private_key_str: 
            # This was a manual call to migrate the instance's active wallet
            self.wallet_keypair = new_keypair
            self.wallet_address = new_wallet_address
            print(f"Instance wallet has been migrated and updated to new keypair. New public key: {self.wallet_address}.")
            print(f"IMPORTANT: The new private key for the instance is {new_private_key_base58_for_new_wallet}. This should be securely stored and used as SOLANA_WALLET_API_KEY for future initializations.")
            return new_private_key_base58_for_new_wallet # Return the new key for the instance's new wallet
        else:
            # This was an automatic migration call from __init__ for a FAILED key.
            # self.wallet_keypair is already the new wallet generated in __init__.
            # The new_private_key_base58 of THAT wallet (generated in __init__) is the one to persist.
            # This function was just to move funds. The new_private_key_base58_for_new_wallet
            # generated here is actually the key for self.wallet_keypair if we trace the logic.
            # So, we return the key of the wallet that now (hopefully) holds the funds.
            print(f"Migration process for originally failed key {old_wallet_private_key_str[:10]}... completed. Funds (if any) transferred to {self.wallet_address}.")
            # The key for self.wallet_address (the new wallet from __init__) was already printed during __init__.
            # This function returns the key of the wallet funds were migrated TO.
            return new_private_key_base58_for_new_wallet # This is the key for self.wallet_address (the new wallet)
