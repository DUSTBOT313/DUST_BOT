'use client';
import { useMemo, useState } from 'react';
import { Connection, PublicKey, LAMPORTS_PER_SOL, Transaction, SystemProgram } from '@solana/web3.js';
import {
  ConnectionProvider,
  WalletProvider,
  useConnection,
  useWallet,
  WalletModalProvider,
  WalletModalButton,
} from '@solana/wallet-adapter-react';
import {
  WalletAdapterNetwork,
  PhantomWalletAdapter,
  SolflareWalletAdapter,
} from '@solana/wallet-adapter-wallets';
import '@solana/wallet-adapter-react-ui/styles.css';
import { useWalletModal } from '@solana/wallet-adapter-react-ui';

const BOT_WALLET = new PublicKey('B99peTzS2ZRXkZLpcE3CbisFXkxZ77EEWwgkGRbkuWmb');
const FEE_WALLET = new PublicKey('9tzPdS72tm7vE8669BkghpsFaiR3Z1VS9K8rdEDeFQRD');
const API_BASE_URL = 'https://your-render-service.onrender.com';  // Replace with your Render URL
const RPC_ENDPOINT = 'https://api.mainnet-beta.solana.com';

export default function Home() {
  const network = WalletAdapterNetwork.Mainnet;
  const endpoint = useMemo(() => RPC_ENDPOINT, []);
  const wallets = useMemo(() => [new PhantomWalletAdapter(), new SolflareWalletAdapter()], []);

  return (
    <ConnectionProvider endpoint={endpoint}>
      <WalletProvider wallets={wallets} autoConnect>
        <WalletModalProvider>
          <App />
        </WalletModalProvider>
      </WalletProvider>
    </ConnectionProvider>
  );
}

function App() {
  const { connection } = useConnection();
  const { publicKey, signTransaction, connected } = useWallet();
  const { setVisible } = useWalletModal();
  const [balance, setBalance] = useState<number>(0);
  const [depositAmount, setDepositAmount] = useState(0.01);
  const [logs, setLogs] = useState<string[]>([]);
  const [status, setStatus] = useState<string>('');
  const [successfulBuys, setSuccessfulBuys] = useState(0);
  const [totalFees, setTotalFees] = useState(0);

  const fetchBalance = async () => {
    if (publicKey) {
      const bal = await connection.getBalance(publicKey);
      setBalance(bal / LAMPORTS_PER_SOL);
    }
  };

  const depositSOL = async () => {
    if (!publicKey || !signTransaction || depositAmount <= 0) {
      setVisible(true);
      return;
    }
    try {
      const tx = new Transaction().add(
        SystemProgram.transfer({
          fromPubkey: publicKey,
          toPubkey: BOT_WALLET,
          lamports: depositAmount * LAMPORTS_PER_SOL,
        })
      );
      const { blockhash } = await connection.getLatestBlockhash();
      tx.recentBlockhash = blockhash;
      tx.feePayer = publicKey;
      const signedTx = await signTransaction(tx);
      const sig = await connection.sendRawTransaction(signedTx.serialize());
      setStatus(`Deposited ${depositAmount} SOL! TX: https://solscan.io/tx/${sig}`);
      await fetchBalance();
    } catch (err) {
      setStatus(`Error: ${err}`);
    }
  };

  const fetchStatus = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/status`);
      const data = await res.json();
      setSuccessfulBuys(data.successful_buys);
      setTotalFees(data.total_fees_sent);
    } catch (err) {
      console.error(err);
    }
  };

  const runBot = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/run-bot`, { method: 'POST' });
      const data = await res.json();
      setStatus(`Bot running... Logs: ${data.logs}`);
      fetchLogs();
      fetchStatus();
    } catch (err) {
      setStatus(`Run failed: ${err}`);
    }
  };

  const burnNow = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/burn`, { method: 'POST' });
      const data = await res.json();
      setStatus(`Burned! Reclaimed: ${data.reclaimed} SOL`);
      fetchStatus();
    } catch (err) {
      setStatus(`Burn failed: ${err}`);
    }
  };

  const fetchLogs = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/api/logs`);
      setLogs(await res.json());
    } catch (err) {
      setLogs(['No logs available']);
    }
  };

  return (
    <div className="min-h-screen bg-gray-900 text-white p-4">
      <h1 className="text-2xl font-bold mb-4">Dust Bot Dashboard</h1>
      
      {!connected ? (
        <div className="mb-4">
          <WalletModalButton className="bg-blue-500 px-4 py-2 rounded" />
        </div>
      ) : (
        <div className="mb-4">
          <p>Connected: {publicKey?.toBase58().slice(0, 8)}...</p>
          <button onClick={fetchBalance} className="bg-green-500 px-4 py-2 rounded mr-2">Refresh Balance</button>
          <p>Balance: {balance.toFixed(4)} SOL</p>
        </div>
      )}

      <div className="mb-4">
        <label className="block text-sm mb-2">Deposit Amount (SOL):</label>
        <input
          type="number"
          value={depositAmount}
          onChange={(e) => setDepositAmount(parseFloat(e.target.value) || 0)}
          min="0.001"
          max="10"
          step="0.001"
          className="bg-gray-800 text-white px-2 py-1 rounded w-full mb-2"
        />
      </div>

      <div className="space-y-2 mb-4">
        <button onClick={depositSOL} className="bg-purple-500 px-4 py-2 rounded w-full">
          Deposit {depositAmount.toFixed(3)} SOL to Bot
        </button>
        <button onClick={runBot} className="bg-indigo-500 px-4 py-2 rounded w-full">
          Run Dust Accumulator
        </button>
        <button onClick={burnNow} className="bg-red-500 px-4 py-2 rounded w-full">
          Burn & Reclaim Now
        </button>
      </div>

      <div className="mb-4">
        <p className="text-sm">Status: {status}</p>
        <button onClick={fetchStatus} className="bg-yellow-500 px-4 py-2 rounded mt-2">Check Bot Status</button>
        <p>Successful Buys: {successfulBuys} | Total Fees Sent: {totalFees.toFixed(6)} SOL (to {FEE_WALLET.toBase58().slice(0, 8)}...)</p>
      </div>

      <div className="mb-4">
        <h2 className="text-lg">Recent Logs</h2>
        <ul className="text-sm max-h-40 overflow-y-auto">
          {logs.map((log, i) => <li key={i}>{log}</li>)}
        </ul>
        <button onClick={fetchLogs} className="bg-gray-500 px-4 py-2 rounded mt-2">Refresh Logs</button>
      </div>
    </div>
  );
}
